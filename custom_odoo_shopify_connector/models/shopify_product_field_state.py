from odoo import fields, models


class ShopifyProductFieldState(models.Model):
    _name = "shopify.product.field.state"
    _description = "Shopify Product Field Sync State"
    _rec_name = "field_name"

    store_id = fields.Many2one("shopify.store", required=True, ondelete="cascade", index=True)
    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    field_name = fields.Char(required=True, index=True)
    last_source = fields.Selection(
        [("odoo", "Odoo"), ("shopify", "Shopify")],
        default="odoo",
        required=True,
    )
    local_updated_at = fields.Datetime()
    remote_updated_at = fields.Datetime()

    _sql_constraints = [
        (
            "shopify_product_field_state_unique",
            "unique(store_id, product_tmpl_id, field_name)",
            "Field sync state must be unique per store/product/field.",
        )
    ]
