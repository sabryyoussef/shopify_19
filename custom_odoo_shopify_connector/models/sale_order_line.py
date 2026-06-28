from odoo import fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    shopify_line_item_id = fields.Char(
        string="Shopify Line Item ID",
        index=True,
        help="Shopify line_item id used for refund and exchange mapping.",
    )
    shopify_original_price = fields.Float(
        string="Shopify Original Price",
        help="Unit price from Shopify before discounts.",
    )
    shopify_line_discount_amount = fields.Float(
        string="Shopify Line Discount",
        help="Total discount amount allocated to this line from Shopify.",
    )
    shopify_refunded_qty = fields.Float(
        string="Shopify Refunded Qty",
        default=0.0,
        help="Cumulative quantity refunded in Shopify for this line.",
    )
