from odoo import fields, models


class ShopifyVariantMap(models.Model):
    _name = "shopify.variant.map"
    _description = "Shopify Product Variant Mapping"

    store_id = fields.Many2one("shopify.store", required=True, ondelete="cascade")
    shopify_product_id = fields.Char(required=True, index=True)
    shopify_variant_id = fields.Char(required=True, index=True)
    shopify_inventory_item_id = fields.Char(index=True)

    product_id = fields.Many2one(
        "product.product",
        string="Odoo Variant",
        required=True,
        ondelete="cascade",
    )

    # Stock export tracking (duplicate protection)
    last_synced_qty = fields.Float(
        string="Last Synced Quantity",
        help="Last quantity exported to Shopify for this variant.",
    )
    last_synced_at = fields.Datetime(
        string="Last Synced At",
        help="Last time stock was synced to Shopify for this variant.",
    )

    # Price export tracking (duplicate protection)
    last_synced_price = fields.Float(
        string="Last Synced Price",
        help="Last price exported to Shopify for this variant.",
    )

    _shopify_variant_store_unique = models.Constraint(
        "UNIQUE(store_id, shopify_variant_id)",
        "Each Shopify variant must be mapped only once per store.",
    )

