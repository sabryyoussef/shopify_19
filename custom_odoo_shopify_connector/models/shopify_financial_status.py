from odoo import fields, models


class ShopifyFinancialStatus(models.Model):
    _name = "shopify.financial.status"
    _description = "Shopify Financial Status Rule"
    _order = "instance_id, payment_gateway_id, shopify_financial_status"

    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
        ondelete="cascade",
        help="Shopify store (instance) this rule applies to.",
    )

    payment_gateway_id = fields.Many2one(
        "shopify.payment.gateway",
        string="Payment Gateway",
        required=True,
        ondelete="cascade",
    )

    shopify_financial_status = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("authorized", "Authorized"),
            ("paid", "Paid"),
            ("partially_paid", "Partially Paid"),
            ("refunded", "Refunded"),
            ("voided", "Voided"),
        ],
        string="Shopify Financial Status",
        required=True,
    )

    workflow_id = fields.Many2one(
        "shopify.sale.auto.workflow",
        string="Sales Auto Workflow",
        help="Sales auto workflow to apply when this rule matches.",
    )

    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "shopify_financial_status_unique_rule",
            "unique(instance_id, payment_gateway_id, shopify_financial_status)",
            "There is already a financial status rule defined for this "
            "store, payment gateway and financial status.",
        )
    ]

