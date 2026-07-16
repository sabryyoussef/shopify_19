import logging
from odoo import fields

_logger = logging.getLogger(__name__)


class ShopifyFulfillmentService:

    def __init__(self, env):
        self.env = env

    # ---------------------------
    # IDEMPOTENCY
    # ---------------------------
    def _is_already_processed(self, order, payload):
        fulfillment_ids = [str(f.get("id")) for f in (payload.get("fulfillments") or []) if f.get("id")]

        if not fulfillment_ids:
            return False

        existing = (order.shopify_fulfillment_ids or "").split(",")
        return any(fid in existing for fid in fulfillment_ids)

    def _mark_processed(self, order, payload):
        fulfillment_ids = [str(f.get("id")) for f in (payload.get("fulfillments") or []) if f.get("id")]
        if not fulfillment_ids:
            return

        existing = set((order.shopify_fulfillment_ids or "").split(","))
        existing.update(fulfillment_ids)

        order.shopify_fulfillment_ids = ",".join(filter(None, existing))

    # ---------------------------
    # PICKINGS
    # ---------------------------
    def _get_pickings(self, order):
        return order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))

    def _ensure_reserved(self, pickings):
        for picking in pickings:
            if picking.state not in ("assigned", "confirmed"):
                picking.action_assign()

    # ---------------------------
    # FULFILLMENT MAP
    # ---------------------------
    def _build_fulfilled_map(self, payload):
        result = {}

        for fulfillment in payload.get("fulfillments") or []:
            location_id = str(fulfillment.get("location_id") or "")

            for line in fulfillment.get("line_items") or []:
                key = (
                    str(line.get("variant_id") or "") or None,
                    line.get("sku") or None,
                    location_id or None,
                )

                try:
                    qty = float(line.get("quantity") or 0.0)
                except Exception:
                    qty = 0.0

                result[key] = result.get(key, 0.0) + qty

        return result

    # ---------------------------
    # MATCHING
    # ---------------------------
    def _match_qty(self, product, fulfilled_map):
        variant_id = str(getattr(product, "shopify_variant_id", "") or "") or None
        sku = product.default_code or None

        # Try strongest → weakest
        for key in [
            (variant_id, sku, None),
            (variant_id, None, None),
            (None, sku, None),
        ]:
            if key in fulfilled_map:
                return fulfilled_map[key]

        return 0.0

    def _ml_demand_qty(self, ml):
        """Odoo 19 uses stock.move.line.quantity; older versions used product_uom_qty/qty_done."""
        if "quantity" in ml._fields:
            return float(ml.quantity or 0.0)
        return float(getattr(ml, "product_uom_qty", 0.0) or 0.0)

    def _ml_done_qty(self, ml):
        if "picked" in ml._fields:
            return float(ml.quantity or 0.0) if ml.picked else 0.0
        return float(getattr(ml, "qty_done", 0.0) or 0.0)

    def _ml_set_done(self, ml, qty):
        vals = {}
        if "quantity" in ml._fields:
            vals["quantity"] = qty
        if "picked" in ml._fields:
            vals["picked"] = bool(qty)
        if "qty_done" in ml._fields:
            vals["qty_done"] = qty
        if vals:
            ml.write(vals)

    # ---------------------------
    # APPLY PARTIAL
    # ---------------------------
    def _apply_partial(self, order, pickings, payload):
        fulfilled_map = self._build_fulfilled_map(payload)
        if not fulfilled_map:
            return

        self._ensure_reserved(pickings)

        for line in order.order_line:
            fulfilled_qty = self._match_qty(line.product_id, fulfilled_map)
            if not fulfilled_qty:
                continue

            remaining = fulfilled_qty

            for picking in pickings:
                for move in picking.move_ids.filtered(lambda m: m.product_id == line.product_id):
                    for ml in move.move_line_ids:
                        if remaining <= 0:
                            break

                        demand = self._ml_demand_qty(ml) or float(move.product_uom_qty or 0.0)
                        done = self._ml_done_qty(ml)
                        available = demand - done
                        if available <= 0:
                            # still allow setting from move demand when line quantity is empty
                            available = float(move.product_uom_qty or 0.0) - done
                        if available <= 0:
                            continue

                        to_apply = min(available, remaining)
                        self._ml_set_done(ml, min(demand or (done + to_apply), done + to_apply))
                        remaining -= to_apply

        # validate only if something done
        for picking in pickings:
            if any(self._ml_done_qty(ml) > 0 for ml in picking.move_line_ids):
                picking.button_validate()

    # ---------------------------
    # APPLY FULL
    # ---------------------------
    def _apply_full(self, pickings):
        self._ensure_reserved(pickings)

        for picking in pickings:
            for ml in picking.move_line_ids:
                demand = self._ml_demand_qty(ml)
                if not demand and ml.move_id:
                    demand = float(ml.move_id.product_uom_qty or 0.0)
                if demand and not self._ml_done_qty(ml):
                    self._ml_set_done(ml, demand)
            picking.button_validate()

    # ---------------------------
    # REFUND HANDLING (LIGHT)
    # ---------------------------
    def _apply_refund_guard(self, order):
        """
        Block fulfillment only when every shippable line is fully refunded.
        """
        shippable_lines = order.order_line.filtered(
            lambda l: not l.display_type and l.product_id.type != "service"
        )
        if not shippable_lines:
            if getattr(order, "shopify_refunded", False):
                _logger.warning("Skipping fulfillment: order marked fully refunded")
                return True
            return False

        all_refunded = all(
            (line.shopify_refunded_qty or 0.0) >= line.product_uom_qty
            for line in shippable_lines
        )
        if all_refunded or (
            getattr(order, "shopify_refunded", False)
            and not any(line.shopify_refunded_qty for line in shippable_lines)
        ):
            _logger.warning("Skipping fulfillment: all lines refunded")
            return True
        return False

    # ---------------------------
    # MAIN ENTRY
    # ---------------------------
    def handle_fulfillment(self, order, store, payload):
        if not order or not payload:
            return

        # Idempotency check
        if self._is_already_processed(order, payload):
            _logger.info("Skipping already processed fulfillment")
            return

        if self._apply_refund_guard(order):
            return

        fulfillment_status = payload.get("fulfillment_status")

        try:
            order.shopify_fulfillment_status = fulfillment_status
        except Exception:
            pass

        pickings = self._get_pickings(order)
        if not pickings:
            return

        if fulfillment_status == "fulfilled":
            self._apply_full(pickings)

        elif fulfillment_status == "partial":
            self._apply_partial(order, pickings, payload)

        # mark processed AFTER success
        self._mark_processed(order, payload)

# from odoo import _


# class ShopifyFulfillmentService:
#     def __init__(self, env):
#         self.env = env

#     def _get_relevant_pickings(self, order):
#         """Return non-done, non-cancelled pickings for the sale order."""
#         return order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))

#     def _apply_full_fulfillment(self, pickings):
#         for picking in pickings:
#             if picking.state not in ("assigned", "confirmed"):
#                 picking.action_assign()
#             for move_line in picking.move_line_ids:
#                 if move_line.product_uom_qty and not move_line.qty_done:
#                     move_line.qty_done = move_line.product_uom_qty
#             picking.button_validate()

#     def _apply_partial_fulfillment(self, order, pickings, payload):
#         """Deliver only fulfilled quantities based on Shopify fulfillments."""
#         fulfillments = payload.get("fulfillments") or []
#         if not fulfillments:
#             return

#         # Map (variant_id, sku) to fulfilled quantity
#         fulfilled_qty_map = {}
#         for fulfillment in fulfillments:
#             for line in fulfillment.get("line_items") or []:
#                 key = (
#                     str(line.get("variant_id") or "") or None,
#                     line.get("sku") or None,
#                 )
#                 try:
#                     qty = float(line.get("quantity") or 0.0)
#                 except Exception:
#                     qty = 0.0
#                 if key not in fulfilled_qty_map:
#                     fulfilled_qty_map[key] = 0.0
#                 fulfilled_qty_map[key] += qty

#         if not fulfilled_qty_map:
#             return

#         # Map sale order lines to fulfilled quantities
#         for line in order.order_line:
#             variant_id = getattr(line.product_id, "shopify_variant_id", False)
#             key = (str(variant_id) if variant_id else None, line.product_id.default_code)
#             fulfilled_qty = fulfilled_qty_map.get(key)
#             if not fulfilled_qty:
#                 continue

#             remaining = fulfilled_qty
#             for move in pickings.move_ids.filtered(
#                 lambda m: m.product_id == line.product_id
#             ):
#                 for move_line in move.move_line_ids:
#                     if remaining <= 0:
#                         break
#                     qty_available = move_line.product_uom_qty - move_line.qty_done
#                     if qty_available <= 0:
#                         continue
#                     to_set = min(qty_available, remaining)
#                     move_line.qty_done += to_set
#                     remaining -= to_set

#         for picking in pickings:
#             if any(
#                 line.qty_done > 0.0
#                 for line in picking.move_line_ids
#                 if line.state not in ("done", "cancel")
#             ):
#                 if picking.state not in ("assigned", "confirmed"):
#                     picking.action_assign()
#                 picking.button_validate()

#     def handle_fulfillment(self, order, store, payload):
#         """Apply fulfillment behavior based on Shopify fulfillment status.

#         - fulfilled: create/ensure deliveries and validate them.
#         - partial: deliver only fulfilled quantities.
#         - null: keep pickings open but do not validate.
#         """
#         if not order or not store:
#             return

#         fulfillment_status = (payload or {}).get("fulfillment_status")

#         pickings = self._get_relevant_pickings(order)
#         if not pickings:
#             # Rely on standard stock rules triggered by the sale order workflow.
#             return

#         if fulfillment_status == "fulfilled":
#             self._apply_full_fulfillment(pickings)
#         elif fulfillment_status == "partial":
#             self._apply_partial_fulfillment(order, pickings, payload or {})
#         else:
#             # Do not validate pickings when fulfillment_status is null or unrecognized.
#             return

