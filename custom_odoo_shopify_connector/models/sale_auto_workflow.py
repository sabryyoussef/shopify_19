from odoo import fields, models


class ShopifySaleAutoWorkflow(models.Model):
    _name = "shopify.sale.auto.workflow"
    _description = "Shopify Sales Auto Workflow"

    name = fields.Char(required=True)

    confirm_quotation = fields.Boolean(
        string="Confirm Quotation",
        help="If enabled, sales orders will be automatically confirmed after import.",
    )
    create_invoice = fields.Boolean(
        string="Create Invoice",
        help="If enabled, an invoice will be created automatically after confirmation.",
    )
    validate_invoice = fields.Boolean(
        string="Validate Invoice",
        help="If enabled, created invoices will be posted automatically.",
    )
    register_payment = fields.Boolean(
        string="Register Payment",
        help="If enabled, payments will be registered automatically for validated invoices.",
    )
    force_accounting_date = fields.Boolean(
        string="Force Accounting Date",
        help="If enabled, the invoice accounting date will be forced to match the order date.",
    )

    payment_journal_id = fields.Many2one(
        "account.journal",
        string="Payment Journal",
        domain="[('type', 'in', ('bank', 'cash'))]",
    )
    payment_method_id = fields.Many2one(
        "account.payment.method",
        string="Payment Method",
    )
    sales_journal_id = fields.Many2one(
        "account.journal",
        string="Sales Journal",
        domain="[('type', '=', 'sale')]",
        help="Sales journal to use when creating invoices.",
    )

    shipment_policy = fields.Selection(
        [
            ("deliver_each_product", "Deliver Each Product"),
            ("deliver_all_at_once", "Deliver All at Once"),
        ],
        string="Shipment Policy",
        default="deliver_each_product",
        help="Defines how deliveries should be created for imported orders.",
    )

