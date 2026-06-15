from odoo import fields, models


class ShopifyProductQueueLine(models.Model):
    _name = "shopify.product.queue.line"
    _description = "Shopify Product Queue Line"
    _order = "id asc"

    queue_id = fields.Many2one(
        "shopify.product.queue",
        string="Product Queue",
        required=True,
        ondelete="cascade",
        index=True,
    )
    shopify_product_id = fields.Char(string="Product Data ID", required=True)
    shopify_sku = fields.Char(string="Shopify SKU")
    title = fields.Char(string="Product")
    price = fields.Float(string="Price")
    image_import_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ],
        string="Image Import State",
        default="pending",
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="State",
        default="pending",
        required=True,
        index=True,
    )
    message = fields.Text(string="Message")
