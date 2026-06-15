from odoo import fields, models, _
from odoo.exceptions import UserError


class ShopifyRefundWizard(models.TransientModel):
    _name = "shopify.refund.wizard"
    _description = "Shopify Refund Wizard"

    move_id = fields.Many2one("account.move", string="Credit Note", required=True, ondelete="cascade")
    sale_order_id = fields.Many2one("sale.order", string="Sale Order", required=True, ondelete="cascade")
    store_id = fields.Many2one("shopify.store", string="Store", required=True, ondelete="cascade")

    refund_note = fields.Text(string="Refund Note")
    notify = fields.Boolean(string="Notify Customer", default=True)

    def action_refund_in_shopify(self):
        self.ensure_one()
        move = self.move_id
        if move.move_type != "out_refund":
            raise UserError(_("This wizard can only be used for customer credit notes."))
        move.action_refund_shopify(refund_note=self.refund_note, notify=self.notify)
        return {"type": "ir.actions.act_window_close"}

