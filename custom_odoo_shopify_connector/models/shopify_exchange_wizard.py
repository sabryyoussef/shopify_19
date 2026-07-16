from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_round


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
    total_return_amount = fields.Float(
        string="Return Total",
        compute="_compute_amounts",
    )
    total_replacement_amount = fields.Float(
        string="Replacement Total",
        compute="_compute_amounts",
    )
    price_difference = fields.Float(
        string="Price Difference",
        compute="_compute_amounts",
        help="Positive = customer pays more; negative = additional credit due.",
    )

    @api.depends("line_ids.return_qty", "line_ids.replacement_qty", "line_ids.replacement_price_unit", "line_ids.sale_line_id")
    def _compute_amounts(self):
        for wiz in self:
            return_total = 0.0
            replacement_total = 0.0
            for line in wiz.line_ids:
                sol = line.sale_line_id
                if not sol:
                    continue
                return_total += (
                    sol.price_unit
                    * line.return_qty
                    * (1 - (sol.discount or 0.0) / 100.0)
                )
                replacement_total += (line.replacement_price_unit or 0.0) * line.replacement_qty
            wiz.total_return_amount = float_round(return_total, 2)
            wiz.total_replacement_amount = float_round(replacement_total, 2)
            wiz.price_difference = float_round(replacement_total - return_total, 2)

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
                replacement_product = sol.product_id
                lines.append(
                    (
                        0,
                        0,
                        {
                            "sale_line_id": sol.id,
                            "return_qty": remaining,
                            "replacement_product_id": replacement_product.id,
                            "replacement_qty": remaining,
                            "replacement_price_unit": replacement_product.lst_price,
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
        exchange_key = "exchange-%s" % order.id
        if order.shopify_exchange_key == exchange_key or order.shopify_exchange_child_ids:
            raise UserError(_("An exchange has already been processed for this order."))
        if refund_service._refund_already_processed(exchange_key):
            raise UserError(_("An exchange credit note already exists for this order."))

        invoice = refund_service._find_posted_invoice(order)
        if not invoice:
            raise UserError(_("No posted invoice found for this order."))

        refund_line_items = []
        replacement_lines = []
        return_total = 0.0
        replacement_total = 0.0

        for wiz_line in self.line_ids:
            if wiz_line.return_qty <= 0:
                continue
            sol = wiz_line.sale_line_id
            if not sol.shopify_line_item_id:
                raise UserError(
                    _("Line %s has no Shopify line item id; re-import the order first.")
                    % sol.name
                )
            line_return_amount = (
                sol.price_unit
                * wiz_line.return_qty
                * (1 - (sol.discount or 0.0) / 100.0)
            )
            line_replacement_amount = (wiz_line.replacement_price_unit or 0.0) * wiz_line.replacement_qty
            return_total += line_return_amount
            replacement_total += line_replacement_amount

            refund_line_items.append(
                {
                    "line_item_id": sol.shopify_line_item_id,
                    "quantity": wiz_line.return_qty,
                    "subtotal": line_return_amount,
                }
            )
            replacement_lines.append(
                {
                    "product_id": wiz_line.replacement_product_id.id,
                    "name": wiz_line.replacement_product_id.display_name,
                    "product_uom_qty": wiz_line.replacement_qty,
                    "price_unit": wiz_line.replacement_price_unit,
                }
            )

        if not refund_line_items:
            raise UserError(_("Select at least one line to return."))

        refund_payload = {
            "id": exchange_key,
            "refund_line_items": refund_line_items,
            "note": self.note or _("Exchange"),
        }
        mapped = refund_service._map_refund_lines(order, refund_line_items)
        price_diff = float_round(replacement_total - return_total, 2)
        if price_diff < 0:
            mapped.append(
                {
                    "sale_line": False,
                    "qty": 1.0,
                    "amount": abs(price_diff),
                    "line_item_id": "exchange-price-adjustment",
                }
            )

        # Create CN first; if replacement SO fails the CN keeps shopify_refund_id
        # so a second attempt is blocked by the idempotency guard above.
        credit = refund_service._create_partial_credit_note(
            store, order, invoice, refund_payload, mapped
        )
        if store.refund_restock_mode in ("odoo_restock", "both"):
            from ..services.return_picking_service import ReturnPickingService

            ReturnPickingService(self.env).create_return_from_refund(
                store, order, refund_payload, mapped
            )

        try:
            replacement_order = self.env["sale.order"].create(
                {
                    "partner_id": order.partner_id.id,
                    "company_id": order.company_id.id,
                    "shopify_instance_id": store.id,
                    "shopify_exchange_parent_id": order.id,
                    "shopify_exchange_price_diff": price_diff,
                    "origin": _("Exchange for %s") % order.name,
                    "client_order_ref": _("EXCHANGE-%s") % (order.client_order_ref or order.name),
                }
            )
            for line_vals in replacement_lines:
                line_vals["order_id"] = replacement_order.id
                self.env["sale.order.line"].create(line_vals)

            if price_diff > 0:
                balance_product = store.payment_fee_product_id or self.env["product.product"].search(
                    [("default_code", "=", "EXCHANGE_BALANCE")], limit=1
                )
                if not balance_product:
                    balance_product = self.env["product.product"].create(
                        {
                            "name": "Exchange Balance Due",
                            "default_code": "EXCHANGE_BALANCE",
                            "type": "service",
                            "list_price": 0.0,
                        }
                    )
                self.env["sale.order.line"].create(
                    {
                        "order_id": replacement_order.id,
                        "product_id": balance_product.id,
                        "name": _("Exchange balance due (customer owes)"),
                        "product_uom_qty": 1.0,
                        "price_unit": price_diff,
                    }
                )
        except Exception:
            # Leave CN in place; mark exchange key so retry is explicit admin action
            order.write({"shopify_exchange_key": exchange_key, "shopify_exchange_price_diff": price_diff})
            raise

        order.write(
            {
                "shopify_exchange_key": exchange_key,
                "shopify_exchange_price_diff": price_diff,
            }
        )

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
    replacement_price_unit = fields.Float(
        string="Replacement Unit Price",
        required=True,
        help="Unit price charged on the replacement order (defaults to product list price).",
    )
    original_unit_price = fields.Float(
        string="Original Unit Price",
        related="sale_line_id.price_unit",
        readonly=True,
    )
