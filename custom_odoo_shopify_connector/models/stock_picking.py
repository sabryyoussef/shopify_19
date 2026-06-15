# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models

from ..services.shipping_service import ShopifyShippingService


class StockPicking(models.Model):
    _inherit = "stock.picking"

    # Redefine with index for performance (10k+ orders)
    carrier_tracking_ref = fields.Char(string="Tracking Reference", copy=False, index=True)
    shopify_tracking_reference = fields.Char(
        string="Shopify Tracking Reference",
        related="carrier_tracking_ref",
        store=True,
        readonly=False,
        help="Alias of Tracking Reference used by Shopify sync workflow.",
    )

    shopify_shipping_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="Shopify Shipping Status",
        default="pending",
        index=True,
        help="Tracks whether shipping information for this delivery has been sent to Shopify.",
    )

    shopify_fulfilled = fields.Boolean(
        string="Shopify Fulfilled",
        compute="_compute_shopify_fulfilled",
        store=True,
        index=True,
        help="True when shipping info has been successfully sent to Shopify.",
    )

    shopify_has_tracking = fields.Boolean(
        string="Has Tracking to Sync",
        compute="_compute_shopify_has_tracking",
        store=True,
        index=True,
        help="True when this picking has tracking (on picking or on packages) to send to Shopify.",
    )

    @api.depends("carrier_tracking_ref", "move_line_ids.result_package_id.carrier_tracking_ref")
    def _compute_shopify_has_tracking(self):
        for picking in self:
            ref = (picking.carrier_tracking_ref or "").strip()
            if ref:
                picking.shopify_has_tracking = True
                continue
            packages = (picking.move_line_ids or self.env["stock.move.line"]).mapped("result_package_id")
            picking.shopify_has_tracking = any(
                (pkg.carrier_tracking_ref or "").strip()
                for pkg in packages
                if pkg
            )

    @api.depends("shopify_shipping_status")
    def _compute_shopify_fulfilled(self):
        for picking in self:
            picking.shopify_fulfilled = picking.shopify_shipping_status == "done"

    def _get_shopify_sale_order(self):
        """Return the sale order linked to this picking that has a Shopify order id."""
        self.ensure_one()
        SaleOrder = self.env["sale.order"]

        sale = getattr(self, "sale_id", False) or False
        if sale and sale.shopify_order_id:
            return sale

        origin = (self.origin or "").strip()
        if origin:
            sale = SaleOrder.search(
                [("name", "=", origin), ("shopify_order_id", "!=", False)],
                limit=1,
            )
            if sale:
                return sale

        return SaleOrder.browse()

    def action_update_shopify_shipping(self):
        """
        Update order shipping status on Shopify (fulfillment API).
        Called by the "Update Order Shipping Status" button and by the scheduler.
        Duplicate fulfillment is avoided: already fulfilled pickings are skipped.
        """
        for picking in self:
            if picking.state != "done":
                continue
            if picking.shopify_fulfilled:
                continue
            sale = picking._get_shopify_sale_order()
            if not sale or not sale.shopify_order_id or not sale.shopify_instance_id:
                continue
            ShopifyShippingService(self.env).update_shipping(picking, sale, sale.shopify_instance_id)
        return True

    def action_update_shopify_shipping_status(self):
        """Alias for action_update_shopify_shipping (backward compatibility)."""
        return self.action_update_shopify_shipping()

    @api.model
    def cron_shopify_update_shipping_status(self):
        """
        Cron: find done pickings with tracking and shopify_fulfilled = False,
        then sync to Shopify. Runs every 30 minutes. Supports 10k+ orders with
        duplicate fulfillment protection and API retry via the shipping service.
        """
        domain = [
            ("state", "=", "done"),
            ("shopify_fulfilled", "=", False),
            ("shopify_has_tracking", "=", True),
        ]
        pickings = self.search(domain)
        for picking in pickings:
            sale = picking._get_shopify_sale_order()
            if not sale or not sale.shopify_order_id or not sale.shopify_instance_id:
                continue
            try:
                ShopifyShippingService(self.env).update_shipping(picking, sale, sale.shopify_instance_id)
            except Exception:
                continue
