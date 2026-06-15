from odoo import fields, models


class ShopifyOrderMap(models.Model):
    _name = "shopify.order.map"
    _description = "Shopify Order Mapping"
    _order = "id desc"

    store_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
        ondelete="cascade",
        index=True,
    )
    shopify_order_id = fields.Char(
        string="Shopify Order ID",
        required=True,
        index=True,
    )
    odoo_order_id = fields.Many2one(
        "sale.order",
        string="Odoo Sale Order",
        required=True,
        ondelete="cascade",
    )

    _sql_constraints = [
        (
            "shopify_order_map_unique",
            "unique(store_id, shopify_order_id)",
            "Shopify order already mapped for this store.",
        )
    ]

