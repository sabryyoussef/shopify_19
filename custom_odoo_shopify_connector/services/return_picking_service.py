import logging

from odoo import _

_logger = logging.getLogger(__name__)


class ReturnPickingService:
    """Create Odoo return pickings from Shopify refund line items."""

    def __init__(self, env):
        self.env = env

    def _log(self, store, message, payload, status="success", order_id=False):
        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="refund",
            message=message,
            payload=payload,
            status=status,
            order_id=order_id,
        )

    def _resolve_warehouse(self, store, shopify_location_id):
        if shopify_location_id:
            location = self.env["shopify.location"].search(
                [
                    ("shopify_store_id", "=", store.id),
                    ("shopify_location_id", "=", str(shopify_location_id)),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if location and location.warehouse_id:
                return location.warehouse_id
            if location and location.order_warehouse_id:
                return location.order_warehouse_id
        if store.default_return_warehouse_id:
            return store.default_return_warehouse_id
        return self.env["stock.warehouse"].search(
            [("company_id", "=", store.company_id.id)], limit=1
        )

    def _should_restock(self, store, restock_type):
        mode = store.refund_restock_mode or "credit_note_only"
        if mode == "credit_note_only":
            return False
        return (restock_type or "").lower() in ("return", "cancel")

    def create_return_from_refund(self, store, order, refund_payload, mapped_lines):
        """Create incoming pickings for refund lines when store config allows."""
        if not store or not order:
            return self.env["stock.picking"]

        refund_line_items = refund_payload.get("refund_line_items") or []
        restock_by_line = {
            str(rli.get("line_item_id")): (rli.get("restock_type") or "no_restock")
            for rli in refund_line_items
        }
        warehouse = self._resolve_warehouse(store, refund_payload.get("location_id"))
        if not warehouse:
            self._log(
                store,
                _("Refund restock skipped: no warehouse configured for order %s.") % order.name,
                refund_payload,
                status="failed",
                order_id=order.shopify_order_id,
            )
            return self.env["stock.picking"]

        picking_type = warehouse.in_type_id
        if not picking_type:
            return self.env["stock.picking"]

        pickings = self.env["stock.picking"]
        for entry in mapped_lines:
            sale_line = entry.get("sale_line")
            qty = entry.get("qty") or 0.0
            if not sale_line or qty <= 0:
                continue
            restock_type = restock_by_line.get(entry.get("line_item_id") or "", "no_restock")
            if not self._should_restock(store, restock_type):
                continue

            picking = self.env["stock.picking"].create(
                {
                    "picking_type_id": picking_type.id,
                    "location_id": picking_type.default_location_src_id.id,
                    "location_dest_id": picking_type.default_location_dest_id.id,
                    "origin": _("Shopify refund %s") % order.name,
                    "sale_id": order.id,
                }
            )
            self.env["stock.move"].create(
                {
                    "product_id": sale_line.product_id.id,
                    "product_uom_qty": qty,
                    "product_uom": sale_line.product_id.uom_id.id,
                    "picking_id": picking.id,
                    "location_id": picking_type.default_location_src_id.id,
                    "location_dest_id": picking_type.default_location_dest_id.id,
                }
            )
            if store.refund_restock_validate:
                picking.action_confirm()
                picking.action_assign()
                for move in picking.move_ids:
                    move.quantity = move.product_uom_qty
                picking.button_validate()
            pickings |= picking

        if pickings:
            self._log(
                store,
                _("Return picking(s) created for Shopify refund on order %s.") % order.name,
                {"picking_ids": pickings.ids},
                order_id=order.shopify_order_id,
            )
        return pickings
