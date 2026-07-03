import logging

from odoo import _
from odoo.tools import float_round

_logger = logging.getLogger(__name__)


class OrderUpdateService:
    """Sync Shopify order edits to existing Odoo sale orders."""

    def __init__(self, env, import_service=None):
        self.env = env
        self.import_service = import_service

    def _log(self, store, message, payload, status="success", order_id=False):
        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message=message,
            payload=payload,
            status=status,
            order_id=order_id,
        )

    def _can_update_lines_in_place(self, order, store):
        mode = store.order_edit_sync_mode or "draft_sent"
        if mode == "ignore":
            return False
        if order.state in ("draft", "sent"):
            return True
        if mode in ("confirmed", "with_adjustments") and order.state == "sale":
            posted = order.invoice_ids.filtered(
                lambda m: m.state == "posted" and m.move_type == "out_invoice"
            )
            return not posted
        return False

    def _update_order_header(self, order, payload):
        vals = {}
        if payload.get("total_price") is not None:
            try:
                vals["shopify_order_total"] = float(payload.get("total_price") or 0.0)
            except (TypeError, ValueError):
                pass
        fulfillment_status = payload.get("fulfillment_status") or "unfulfilled"
        vals["shopify_fulfillment_status"] = fulfillment_status
        gateway_names = payload.get("payment_gateway_names") or []
        if isinstance(gateway_names, str):
            gateway_names = [gateway_names]
        if gateway_names:
            vals["shopify_payment_gateway"] = gateway_names[0]
        elif payload.get("gateway"):
            vals["shopify_payment_gateway"] = payload.get("gateway")
        if vals:
            order.write(vals)

    def _find_line_by_shopify_id(self, order, line_item_id):
        return order.order_line.filtered(
            lambda l: l.shopify_line_item_id == str(line_item_id) and not l.display_type
        )[:1]

    def _sync_lines_in_place(self, order, payload, store):
        from .order_service import OrderService

        order_service = OrderService(self.env, import_service=self.import_service)
        line_items = payload.get("line_items") or []
        payload_ids = {str(item.get("id")) for item in line_items if item.get("id")}

        variant_map_by_id, product_by_variant_id, product_by_sku = order_service._prepare_line_product_lookups(
            store, line_items
        )

        for item in line_items:
            line_item_id = str(item.get("id") or "")
            if not line_item_id:
                continue
            sale_line = self._find_line_by_shopify_id(order, line_item_id)
            quantity = float(item.get("quantity") or 0.0)
            price = float(item.get("price") or 0.0)
            discount_pct = order_service._compute_discount_pct(item, quantity, price)

            if sale_line:
                sale_line.write(
                    {
                        "product_uom_qty": quantity,
                        "price_unit": float_round(price, 2),
                        "discount": discount_pct,
                        "shopify_original_price": float_round(price, 2),
                    }
                )
            else:
                product = order_service._resolve_line_product(
                    item, store, variant_map_by_id, product_by_variant_id, product_by_sku
                )
                vals = order_service._build_order_line_vals(order, item, store, product)
                self.env["sale.order.line"].create(vals)

        for sale_line in order.order_line.filtered(
            lambda l: l.shopify_line_item_id and not l.display_type
        ):
            if sale_line.shopify_line_item_id not in payload_ids:
                sale_line.write({"product_uom_qty": 0.0})

        self._log(
            store,
            _("Shopify order edit synced to sale order %s (in-place).") % order.name,
            {"shopify_order_id": order.shopify_order_id, "line_count": len(line_items)},
            order_id=order.shopify_order_id,
        )

    def _sync_lines_with_adjustments(self, order, payload, store):
        from ..services.refund_sync_service import RefundSyncService

        refund_service = RefundSyncService(self.env)
        invoice = refund_service._find_posted_invoice(order)
        line_items = payload.get("line_items") or []
        refund_line_items = []

        for item in line_items:
            line_item_id = str(item.get("id") or "")
            sale_line = self._find_line_by_shopify_id(order, line_item_id)
            if not sale_line:
                continue
            new_qty = float(item.get("quantity") or 0.0)
            old_qty = sale_line.product_uom_qty
            if new_qty < old_qty and invoice:
                return_qty = old_qty - new_qty
                refund_line_items.append(
                    {
                        "line_item_id": line_item_id,
                        "quantity": return_qty,
                        "subtotal": sale_line.price_unit
                        * return_qty
                        * (1 - (sale_line.discount or 0.0) / 100.0),
                    }
                )
            sale_line.write({"product_uom_qty": new_qty})

        if refund_line_items and invoice:
            refund_payload = {
                "id": "order-edit-%s-%s" % (order.id, payload.get("updated_at") or ""),
                "refund_line_items": refund_line_items,
                "note": _("Shopify order edit adjustment"),
            }
            if not refund_service._refund_already_processed(refund_payload["id"]):
                mapped = refund_service._map_refund_lines(order, refund_line_items)
                refund_service._create_partial_credit_note(
                    store, order, invoice, refund_payload, mapped
                )

        self._log(
            store,
            _("Shopify order edit synced to sale order %s (with adjustments).") % order.name,
            {"shopify_order_id": order.shopify_order_id},
            order_id=order.shopify_order_id,
        )

    def update_order_from_payload(self, order, payload, store):
        if not order or not store:
            return order

        mode = store.order_edit_sync_mode or "draft_sent"
        if mode == "ignore":
            return order

        self._update_order_header(order, payload)

        if self._can_update_lines_in_place(order, store):
            self._sync_lines_in_place(order, payload, store)
        elif mode == "with_adjustments" and order.state == "sale":
            self._sync_lines_with_adjustments(order, payload, store)
        else:
            self._log(
                store,
                _("Shopify order edit received for %s but line sync skipped (state=%s, mode=%s).")
                % (order.name, order.state, mode),
                {"shopify_order_id": order.shopify_order_id},
                status="failed",
                order_id=order.shopify_order_id,
            )

        from .payment_fee_service import PaymentFeeService

        fee_service = PaymentFeeService(self.env, import_service=self.import_service)
        fee_service.apply_fees_for_order(order, store, payload)
        return order
