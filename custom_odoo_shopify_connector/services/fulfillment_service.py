import logging

from . import lifecycle_logger as llog

_logger = logging.getLogger(__name__)

# Shopify fulfillment.status values that mean the fulfillment was reversed.
_REVERSED_STATES = ("cancelled", "canceled", "reversed", "failure", "error")


class ShopifyFulfillmentService:
    """P4 — Shopify fulfillment -> Odoo delivery/picking.

    Shopify is the source of truth for Shopify-originated fulfillment events.
    Guarantees:
      * Idempotent on Shopify fulfillment id (never deliver twice).
      * Only fulfilled quantities are delivered (partial supported); remaining
        stays pending via a backorder.
      * Unmappable fulfillment lines fail safely (no unrelated stock delivered).
      * Reversal after a completed picking is detected and flagged, never
        auto-reverted.
      * Shopify-originated pickings are marked so the Odoo->Shopify shipping
        cron does not echo them back (loop guard).
    """

    def __init__(self, env):
        self.env = env

    # ------------------------------------------------------------------
    # Idempotency (Shopify fulfillment id is the primary key)
    # ------------------------------------------------------------------
    @staticmethod
    def _fulfillment_ids(payload):
        return [str(f.get("id")) for f in (payload.get("fulfillments") or []) if f.get("id")]

    @staticmethod
    def _processed_set(order):
        return set(filter(None, (order.shopify_fulfillment_ids or "").split(",")))

    def _mark_processed(self, order, ids):
        if not ids:
            return
        existing = self._processed_set(order)
        existing.update(str(i) for i in ids)
        order.shopify_fulfillment_ids = ",".join(sorted(filter(None, existing)))

    # ------------------------------------------------------------------
    # Pickings / move lines
    # ------------------------------------------------------------------
    def _get_pickings(self, order):
        return order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))

    def _outgoing_pickings(self, order):
        return order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")

    def _delivery_completed(self, order):
        pickings = self._outgoing_pickings(order)
        if not pickings:
            return False
        return all(p.state == "done" for p in pickings)

    def _ensure_reserved(self, pickings):
        for picking in pickings:
            if picking.state not in ("assigned", "confirmed"):
                picking.action_assign()

    def _ml_demand_qty(self, ml):
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

    # ------------------------------------------------------------------
    # Fulfillment line mapping
    # ------------------------------------------------------------------
    def _build_fulfilled_map(self, fulfillments):
        result = {}
        for fulfillment in fulfillments or []:
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

    def _match_qty(self, product, fulfilled_map):
        variant_id = str(getattr(product, "shopify_variant_id", "") or "") or None
        sku = product.default_code or None
        total = 0.0
        for key, qty in fulfilled_map.items():
            k_variant, k_sku, _loc = key
            if (k_variant and variant_id and k_variant == variant_id) or (
                k_sku and sku and k_sku == sku
            ):
                total += qty
        return total

    def _find_unmapped(self, order, fulfilled_map):
        """Return fulfilled keys that map to no order line (unsafe to deliver)."""
        order_variants = set()
        order_skus = set()
        for line in order.order_line:
            if line.display_type:
                continue
            vid = str(getattr(line.product_id, "shopify_variant_id", "") or "") or None
            if vid:
                order_variants.add(vid)
            if line.product_id.default_code:
                order_skus.add(line.product_id.default_code)
        unmapped = []
        for key, qty in fulfilled_map.items():
            if qty <= 0:
                continue
            k_variant, k_sku, _loc = key
            matched = (k_variant and k_variant in order_variants) or (
                k_sku and k_sku in order_skus
            )
            if not matched:
                unmapped.append((k_variant, k_sku, qty))
        return unmapped

    # ------------------------------------------------------------------
    # Applying quantities
    # ------------------------------------------------------------------
    def _set_map_quantities(self, order, pickings, fulfilled_map):
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
                            available = float(move.product_uom_qty or 0.0) - done
                        if available <= 0:
                            continue
                        to_apply = min(available, remaining)
                        self._ml_set_done(ml, done + to_apply)
                        remaining -= to_apply

    def _set_full_quantities(self, pickings):
        self._ensure_reserved(pickings)
        for picking in pickings:
            for ml in picking.move_line_ids:
                demand = self._ml_demand_qty(ml)
                if not demand and ml.move_id:
                    demand = float(ml.move_id.product_uom_qty or 0.0)
                if demand and not self._ml_done_qty(ml):
                    self._ml_set_done(ml, demand)

    def _validate_pickings_with_done(self, pickings):
        """Validate pickings that have any done qty; auto-create backorders so
        remaining quantities stay pending in a new picking."""
        validated = self.env["stock.picking"]
        for picking in pickings:
            if picking.state in ("done", "cancel"):
                continue
            if not any(self._ml_done_qty(ml) > 0 for ml in picking.move_line_ids):
                continue
            res = picking.button_validate()
            if isinstance(res, dict) and res.get("res_model") == "stock.backorder.confirmation":
                ctx = dict(res.get("context") or {})
                wizard = self.env["stock.backorder.confirmation"].with_context(**ctx).create(
                    {"pick_ids": [(6, 0, picking.ids)]}
                )
                wizard.process()
            validated |= picking
        return validated

    def _mark_shopify_origin(self, order):
        """Mark newly-completed pickings as Shopify-originated (loop guard)."""
        for picking in self._outgoing_pickings(order).filtered(lambda p: p.state == "done"):
            vals = {}
            if not picking.shopify_fulfillment_origin:
                vals["shopify_fulfillment_origin"] = "shopify"
            # Treat as already fulfilled toward Shopify so the push cron skips it.
            if picking.shopify_shipping_status != "done":
                vals["shopify_shipping_status"] = "done"
            if vals:
                picking.write(vals)

    # ------------------------------------------------------------------
    # Refund guard
    # ------------------------------------------------------------------
    def _apply_refund_guard(self, order):
        shippable_lines = order.order_line.filtered(
            lambda l: not l.display_type and l.product_id.type != "service"
        )
        if not shippable_lines:
            if getattr(order, "shopify_refunded", False):
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
            return True
        return False

    # ------------------------------------------------------------------
    # Reversal handling
    # ------------------------------------------------------------------
    def _handle_reversal(self, order, reversed_ids, trace):
        completed = self._delivery_completed(order) or any(
            p.state == "done" for p in self._outgoing_pickings(order)
        )
        note = "Shopify fulfillment %s reversed/cancelled" % ",".join(reversed_ids)
        if completed:
            order.write(
                {
                    "shopify_fulfillment_reversal_flagged": True,
                    "shopify_fulfillment_note": (note + "; picking already done - manual return required")[:255],
                }
            )
            trace.step(
                llog.STEP_FULFILLMENT_REVERSAL_DETECTED,
                so=order.name,
                status="warn",
                level=logging.WARNING,
                fulfillment=",".join(reversed_ids),
                msg="picking already done; flagged for controlled return (no auto-revert)",
            )
            return {"ok": False, "reversal": True, "delivery_completed": True}
        # Not yet delivered -> just record, nothing to revert.
        trace.step(
            llog.STEP_FULFILLMENT_REVERSAL_DETECTED,
            so=order.name,
            status="ok",
            fulfillment=",".join(reversed_ids),
            msg="fulfillment reversed before delivery; no picking to revert",
        )
        return {"ok": False, "reversal": True, "delivery_completed": False}

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    def handle_fulfillment(self, order, store, payload, correlation_id=None, trace=None):
        result = {"ok": False, "delivery_completed": False, "mapping_failed": False,
                  "reversal": False, "applied": False}
        if not order or not payload:
            return result

        trace = trace or llog.LifecycleTrace(
            correlation_id=correlation_id,
            op=llog.OP_FULFILLMENT,
            shop_order=getattr(order, "shopify_order_id", None),
            so=order.name,
        )

        fulfillments = payload.get("fulfillments") or []
        all_ids = self._fulfillment_ids(payload)
        fulfillment_status = payload.get("fulfillment_status")

        trace.step(
            llog.STEP_FULFILLMENT_RECEIVED,
            so=order.name,
            fulfillment=",".join(all_ids) or None,
            msg="status=%s fulfillments=%s" % (fulfillment_status, len(fulfillments)),
        )

        try:
            order.shopify_fulfillment_status = fulfillment_status
        except Exception:
            pass

        # Reversal detection (fulfillment.status cancelled/reversed).
        reversed_ids = [
            str(f.get("id"))
            for f in fulfillments
            if (f.get("status") or "").strip().lower() in _REVERSED_STATES and f.get("id")
        ]
        if reversed_ids:
            return self._handle_reversal(order, reversed_ids, trace)

        if self._apply_refund_guard(order):
            trace.step(llog.STEP_PICKING_UPDATED, so=order.name, status="noop",
                       msg="skipped: order fully refunded")
            return result

        # Idempotency: process only NEW fulfillment ids.
        processed = self._processed_set(order)
        new_fulfillments = (
            [f for f in fulfillments if str(f.get("id")) not in processed]
            if all_ids
            else fulfillments
        )
        new_ids = [str(f.get("id")) for f in new_fulfillments if f.get("id")]
        if all_ids and not new_fulfillments:
            trace.step(
                llog.STEP_FULFILLMENT_DUPLICATE,
                so=order.name,
                status="idempotent",
                fulfillment=",".join(all_ids),
                msg="all fulfillment ids already processed",
            )
            return {"ok": True, "delivery_completed": self._delivery_completed(order),
                    "mapping_failed": False, "reversal": False, "applied": False}

        fulfilled_map = self._build_fulfilled_map(new_fulfillments)

        # Safe mapping check: never deliver unrelated stock for an unmappable line.
        if fulfilled_map:
            unmapped = self._find_unmapped(order, fulfilled_map)
            if unmapped:
                note = "Unmapped fulfillment lines: %s" % unmapped
                order.write(
                    {
                        "shopify_fulfillment_mapping_failed": True,
                        "shopify_fulfillment_note": note[:255],
                    }
                )
                trace.step(
                    llog.STEP_FULFILLMENT_MAPPING_FAILED,
                    so=order.name,
                    status="failed",
                    level=logging.WARNING,
                    fulfillment=",".join(new_ids) or None,
                    msg=note,
                )
                result["mapping_failed"] = True
                return result
            trace.step(
                llog.STEP_FULFILLMENT_MAPPED,
                so=order.name,
                fulfillment=",".join(new_ids) or None,
                msg="mapped %s fulfillment line group(s)" % len(fulfilled_map),
            )

        pickings = self._get_pickings(order)
        if not pickings:
            # Nothing open to deliver (already delivered or no stock moves).
            self._mark_processed(order, new_ids)
            trace.step(llog.STEP_PICKING_UPDATED, so=order.name, status="noop",
                       msg="no open pickings; fulfillment_status=%s" % fulfillment_status)
            return {"ok": True, "delivery_completed": self._delivery_completed(order),
                    "mapping_failed": False, "reversal": False, "applied": False}

        is_full = fulfillment_status == "fulfilled"
        if fulfilled_map:
            self._set_map_quantities(order, pickings, fulfilled_map)
            if is_full:
                # Full fulfillment: ensure any remaining open qty is delivered.
                self._set_full_quantities(self._get_pickings(order))
        elif is_full:
            self._set_full_quantities(pickings)
        else:
            trace.step(llog.STEP_PICKING_UPDATED, so=order.name, status="noop",
                       msg="fulfillment_status=%s (no line detail; no validation)" % fulfillment_status)
            self._mark_processed(order, new_ids)
            return {"ok": True, "delivery_completed": self._delivery_completed(order),
                    "mapping_failed": False, "reversal": False, "applied": False}

        validated = self._validate_pickings_with_done(order.picking_ids.filtered(
            lambda p: p.state not in ("done", "cancel")
        ))
        self._mark_processed(order, new_ids)
        self._mark_shopify_origin(order)

        delivery_completed = self._delivery_completed(order)
        step = llog.STEP_FULFILLMENT_APPLIED if is_full else llog.STEP_PARTIAL_FULFILLMENT_APPLIED
        trace.step(
            step,
            so=order.name,
            fulfillment=",".join(new_ids) or None,
            msg="validated=%s delivery_completed=%s" % (len(validated), delivery_completed),
        )
        for picking in validated:
            trace.step(
                llog.STEP_PICKING_VALIDATED,
                so=order.name,
                status="ok",
                msg="picking=%s state=%s" % (picking.name, picking.state),
            )

        return {"ok": True, "delivery_completed": delivery_completed, "mapping_failed": False,
                "reversal": False, "applied": True}
