from odoo import fields, models


class ShopifyPaymentGateway(models.Model):
    _name = "shopify.payment.gateway"
    _description = "Shopify Payment Gateway"
    _order = "name"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify Gateway ID", index=True)

    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
        ondelete="cascade",
        help="Shopify store (instance) this payment gateway configuration belongs to.",
    )

    odoo_journal_id = fields.Many2one(
        "account.journal",
        string="Payment Journal",
        help="Journal used when registering Shopify payments.",
    )

    payment_code = fields.Char(
        string="Payment Code",
        required=True,
        help="Exact payment gateway identifier from Shopify "
        "(for example: 'paypal', 'cash_on_delivery', 'bank_transfer').",
    )

    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("inactive", "Inactive"),
        ],
        string="State",
        default="active",
    )
    description = fields.Text()

    _sql_constraints = [
        (
            "shopify_gateway_unique_code_instance",
            "unique(instance_id, payment_code)",
            "The payment code must be unique per Shopify store.",
        )
    ]

