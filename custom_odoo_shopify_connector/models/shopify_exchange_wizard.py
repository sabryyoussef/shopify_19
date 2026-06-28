from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ShopifyExchangeWizard(models.TransientModel):
    _name = "shopify.exchange.wizard"
    _description = "Shopify Exchange Wizard"

    sale_order_id = fields.Many2one("sale.order", required=True, ondelete="cascade")
    store_id = fields.Many2one("shopify.store", string="Store", required=True, ondelete="cascade")
    line_ids = fields.One2many(
        "shopify.exchange.wizard.line",
        "wizard_id",
        string="Return Lines",
    )
    note = fields.Text(string="Exchange Note")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order_id = self.env.context.get("default_sale_order_id")
        if order_id and "line_ids" in fields_list:
            order = self.env["sale.order"].browse(order_id)
            lines = []
            for sol in order.order_line.filtered(lambda l: not l.display_type and l.product_id):
                remaining = sol.product_uom_qty - (sol.shopify_refunded_qty or 0.0)
                if remaining <= 0:
                    continue
                lines.append(
                    (
                        0,
                        0,
                        {
                            "sale_line_id": sol.id,
                            "return_qty": remaining,
                            "replacement_product_id": sol.product_id.id,
                            "replacement_qty": remaining,
                        },
                    )
                )
            res["line_ids"] = lines
        return res

    def action_process_exchange(self):
        self.ensure_one()
        order = self.sale_order_id
        store = self.store_id
        if not order.shopify_order_id:
            raise UserError(_("This sale order has no Shopify Order ID."))

        from ..services.refund_sync_service import RefundSyncService

        refund_service = RefundSyncService(self.env)
        invoice = refund_service._find_posted_invoice(order)
        if not invoice:
            raise UserError(_("No posted invoice found for this order."))

        refund_line_items = []
        replacement_lines = []
        for wiz_line in self.line_ids:
            if wiz_line.return_qty <= 0:
                continue
            sol = wiz_line.sale_line_id
            if not sol.shopify_line_item_id:
                raise UserError(
                    _("Line %s has no Shopify line item id; re-import the order first.")
                    % sol.name
                )
            refund_line_items.append(
                {
                    "line_item_id": sol.shopify_line_item_id,
                    "quantity": wiz_line.return_qty,
                    "subtotal": sol.price_unit
                    * wiz_line.return_qty
                    * (1 - (sol.discount or 0.0) / 100.0),
                }
            )
            replacement_lines.append(
                {
                    "product_id": wiz_line.replacement_product_id.id,
                    "name": wiz_line.replacement_product_id.display_name,
                    "product_uom_qty": wiz_line.replacement_qty,
                    "price_unit": sol.price_unit,
                }
            )

        if not refund_line_items:
            raise UserError(_("Select at least one line to return."))

        refund_payload = {
            "id": "exchange-%s" % order.id,
            "refund_line_items": refund_line_items,
            "note": self.note or _("Exchange"),
        }
        mapped = refund_service._map_refund_lines(order, refund_line_items)
        credit = refund_service._create_partial_credit_note(
            store, order, invoice, refund_payload, mapped
        )

        replacement_order = self.env["sale.order"].create(
            {
                "partner_id": order.partner_id.id,
                "company_id": order.company_id.id,
                "shopify_instance_id": store.id,
                "shopify_exchange_parent_id": order.id,
                "origin": _("Exchange for %s") % order.name,
                "client_order_ref": _("EXCHANGE-%s") % order.client_order_ref,
            }
        )
        for line_vals in replacement_lines:
            line_vals["order_id"] = replacement_order.id
            self.env["sale.order.line"].create(line_vals)

        return {
            "type": "ir.actions.act_window",
            "name": _("Replacement Order"),
            "res_model": "sale.order",
            "res_id": replacement_order.id,
            "view_mode": "form",
            "target": "current",
        }


class ShopifyExchangeWizardLine(models.TransientModel):
    _name = "shopify.exchange.wizard.line"
    _description = "Shopify Exchange Wizard Line"

    wizard_id = fields.Many2one("shopify.exchange.wizard", required=True, ondelete="cascade")
    sale_line_id = fields.Many2one("sale.order.line", required=True, ondelete="cascade")
    return_qty = fields.Float(string="Return Qty", required=True, default=1.0)
    replacement_product_id = fields.Many2one("product.product", string="Replacement Product", required=True)
    replacement_qty = fields.Float(string="Replacement Qty", required=True, default=1.0)
