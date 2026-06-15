import json

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ShopifyCancelOrderWizard(models.TransientModel):
    _name = "shopify.cancel.order.wizard"
    _description = "Shopify Cancel Order Wizard"

    sale_order_id = fields.Many2one("sale.order", required=True, ondelete="cascade")
    store_id = fields.Many2one("shopify.store", string="Store", required=True, ondelete="cascade")

    cancel_reason = fields.Selection(
        [
            ("customer", "Customer"),
            ("fraud", "Fraud"),
            ("inventory", "Inventory"),
            ("other", "Other"),
        ],
        string="Cancel Reason",
        required=True,
        default="customer",
    )
    cancel_message = fields.Text(string="Cancel Message")
    email_customer = fields.Boolean(string="Email Customer", default=True)

    def action_cancel_in_shopify(self):
        self.ensure_one()
        order = self.sale_order_id
        store = self.store_id

        if not order.shopify_order_id:
            raise UserError(_("This sale order has no Shopify Order ID."))
        if order.shopify_cancelled:
            return {"type": "ir.actions.act_window_close"}
        if not store:
            raise UserError(_("Shopify instance is missing."))

        payload = {
            "reason": self.cancel_reason,
            "email": bool(self.email_customer),
        }

        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message=_("Cancel request sent to Shopify for order %s") % order.shopify_order_id,
            payload=json.dumps(payload),
            status="success",
            order_id=order.shopify_order_id,
        )

        self.env["shopify.service"].cancel_order_in_shopify(
            store=store,
            shopify_order_id=order.shopify_order_id,
            reason=self.cancel_reason,
            message=self.cancel_message,
            email_customer=self.email_customer,
            sale_order=order,
        )
        return {"type": "ir.actions.act_window_close"}

