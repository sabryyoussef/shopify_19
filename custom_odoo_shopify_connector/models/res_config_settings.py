from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shopify_instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        help="Select the Shopify store instance whose settings you want to configure.",
    )

    # Order configuration (instance-backed)
    shopify_import_order_status = fields.Selection(
        [
            ("unshipped", "Unshipped Orders"),
            ("partially_fulfilled", "Partially Fulfilled Orders"),
        ],
        string="Import Order Status",
        related="shopify_instance_id.import_order_status",
        readonly=False,
    )
    shopify_use_odoo_sequence = fields.Boolean(
        string="Use Odoo Default Sequence",
        related="shopify_instance_id.use_odoo_sequence",
        readonly=False,
    )
    shopify_order_prefix = fields.Char(
        string="Order Prefix",
        related="shopify_instance_id.order_prefix",
        readonly=False,
    )
    shopify_default_pos_customer_id = fields.Many2one(
        "res.partner",
        string="Default POS Customer",
        related="shopify_instance_id.default_pos_customer_id",
        readonly=False,
    )
    shopify_auto_fulfill_gift_card = fields.Boolean(
        string="Automatically Fulfill Gift Card",
        related="shopify_instance_id.auto_fulfill_gift_card",
        readonly=False,
    )

    # Tax configuration (instance-backed)
    shopify_tax_behavior = fields.Selection(
        [
            ("create_tax_if_not_found", "Create New Tax If Not Found"),
            ("use_odoo_tax", "Use Odoo Default Tax"),
        ],
        string="Tax Behavior",
        related="shopify_instance_id.shopify_tax_behavior",
        readonly=False,
    )

    # Webhook configuration (instance-backed)
    shopify_manage_orders_webhook = fields.Boolean(
        string="Manage Orders via Webhook",
        related="shopify_instance_id.manage_orders_webhook",
        readonly=False,
    )

    # Stock configuration (instance-backed)
    shopify_stock_type = fields.Selection(
        [
            ("free_qty", "Free To Use Quantity"),
            ("forecast_qty", "Forecast Quantity"),
        ],
        string="Shopify Stock Type",
        related="shopify_instance_id.shopify_stock_type",
        readonly=False,
    )
    export_stock_enabled = fields.Boolean(
        string="Enable Automatic Stock Export",
        related="shopify_instance_id.export_stock_enabled",
        readonly=False,
    )
    export_stock_interval_number = fields.Integer(
        string="Stock Export Interval",
        related="shopify_instance_id.export_stock_interval_number",
        readonly=False,
    )
    export_stock_interval_type = fields.Selection(
        [
            ("minutes", "Minutes"),
            ("hours", "Hours"),
        ],
        string="Stock Export Interval Unit",
        related="shopify_instance_id.export_stock_interval_type",
        readonly=False,
    )

