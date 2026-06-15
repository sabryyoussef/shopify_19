import json

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    shopify_refunded = fields.Boolean(
        string="Shopify Refunded",
        default=False,
        index=True,
        help="True when this credit note has been pushed to Shopify or created from Shopify webhook.",
    )
    shopify_refund_date = fields.Datetime(
        string="Shopify Refund Date",
        help="Timestamp when this refund was synced.",
        index=True,
    )
    shopify_refund_id = fields.Char(
        string="Shopify Refund ID",
        index=True,
        help="Refund ID received from Shopify (for duplicate protection).",
    )

    def action_open_shopify_refund_wizard(self):
        self.ensure_one()
        if self.move_type != "out_refund":
            raise UserError(_("Refund in Shopify is only available on customer credit notes."))
        if self.state != "posted":
            raise UserError(_("Please post the credit note first."))
        if self.payment_state != "paid":
            raise UserError(_("Please ensure the credit note is paid before refunding in Shopify."))

        sale = self.invoice_origin and self.env["sale.order"].search([("name", "=", self.invoice_origin)], limit=1)
        if not sale or not sale.shopify_order_id or not sale.shopify_instance_id:
            raise UserError(_("No related Shopify order found for this credit note."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Refund in Shopify"),
            "res_model": "shopify.refund.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_move_id": self.id,
                "default_sale_order_id": sale.id,
                "default_store_id": sale.shopify_instance_id.id,
            },
        }

    def action_refund_shopify(self, refund_note=None, notify=True):
        """Called by wizard: push refund to Shopify and mark as synced."""
        for move in self:
            if move.shopify_refunded:
                continue
            if move.move_type != "out_refund" or move.state != "posted" or move.payment_state != "paid":
                continue

            sale = move.invoice_origin and self.env["sale.order"].search([("name", "=", move.invoice_origin)], limit=1)
            if not sale or not sale.shopify_order_id or not sale.shopify_instance_id:
                raise UserError(_("No related Shopify order found for this credit note."))

            payload = {"refund": {"notify": bool(notify), "note": refund_note or ""}}
            store = sale.shopify_instance_id

            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="order",
                message=_("Refund request sent to Shopify for order %s") % sale.shopify_order_id,
                payload=json.dumps(payload),
                status="success",
                order_id=sale.shopify_order_id,
            )

            res = self.env["shopify.service"].refund_order_in_shopify(
                store=store,
                shopify_order_id=sale.shopify_order_id,
                payload=payload,
            )

            move.write(
                {
                    "shopify_refunded": True,
                    "shopify_refund_date": fields.Datetime.now(),
                    "shopify_refund_id": str((res or {}).get("refund", {}).get("id") or "") or False,
                }
            )
            sale.write({"shopify_refunded": True, "shopify_refund_date": fields.Datetime.now()})

        return True

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        SaleOrder = self.env["sale.order"].sudo()
        Store = self.env["shopify.store"].sudo()
        for rec, vals in zip(records, vals_list):
            origin = vals.get("invoice_origin")
            if not origin:
                continue
            sale = SaleOrder.search(
                [("name", "=", origin), ("shopify_order_id", "!=", False)],
                limit=1,
            )
            shopify_user_id = False
            if sale and sale.shopify_instance_id:
                user = sale.shopify_instance_id._resolve_import_order_salesperson_user()
                shopify_user_id = user.id if user else False
            if not shopify_user_id:
                from ..services.order_service import OrderService

                shopify_user_id = OrderService(self.env)._resolve_shopify_user_id(store=None)
            if shopify_user_id:
                rec.invoice_user_id = shopify_user_id
        return records
