from odoo import fields, models


class ShopifyProductMap(models.Model):
    _name = "shopify.product.map"
    _description = "Shopify Product Template Mapping"

    store_id = fields.Many2one("shopify.store", required=True, ondelete="cascade")
    shopify_product_id = fields.Char(required=True, index=True)
    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Odoo Product Template",
        required=True,
        ondelete="cascade",
    )
    checksum = fields.Char(index=True)
    last_sync_at = fields.Datetime()
    idempotency_key = fields.Char(index=True)

    _sql_constraints = [
        (
            "uniq_product_per_store",
            "unique(store_id, product_tmpl_id)",
            "Each product can map only once per store!",
        ),
        (
            "uniq_shopify_product",
            "unique(store_id, shopify_product_id)",
            "Shopify product already mapped!",
        ),
        (
            "uniq_export_idempotency_key",
            "unique(store_id, idempotency_key)",
            "Duplicate product export idempotency key detected!",
        ),
    ]

