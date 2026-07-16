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

    fee_percent = fields.Float(
        string="Fee Percent",
        default=0.0,
        help="Percentage fee charged by this gateway (e.g. 5 for Paymob).",
    )
    fee_fixed = fields.Float(
        string="Fixed Fee",
        default=0.0,
        help="Fixed fee amount per transaction.",
    )
    fee_product_id = fields.Many2one(
        "product.product",
        string="Fee Product",
        help="Product used when adding payment fees as a sale order line.",
    )
    fee_apply_mode = fields.Selection(
        [
            ("line_item", "Add Line Item"),
            ("invoice_discount", "Global Discount at Invoice"),
            ("none", "None"),
        ],
        string="Fee Apply Mode",
        default="none",
    )
    fee_base = fields.Selection(
        [
            ("subtotal", "Order Subtotal"),
            ("order_total", "Order Total"),
        ],
        string="Fee Base",
        default="subtotal",
    )
    # Future accounting options (default keeps current SO fee product / invoice discount behavior).
    fee_recording_mode = fields.Selection(
        [
            ("current", "Current (fee product / invoice discount)"),
            ("expense_split", "Expense split (payment + fee expense + bank net) — not yet active"),
        ],
        string="Fee Recording Mode",
        default="current",
        help="Reserved for accounting change. Default 'current' preserves existing behavior.",
    )
    fee_account_id = fields.Many2one(
        "account.account",
        string="Fee Expense Account",
        help="Optional expense account for Paymob/gateway fees when expense_split is adopted.",
    )
    fee_tax_id = fields.Many2one(
        "account.tax",
        string="Fee Tax",
        help="Optional tax applied on gateway fees when expense_split accounting is adopted.",
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

