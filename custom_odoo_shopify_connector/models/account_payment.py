from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    shopify_transaction_id = fields.Char(
        string="Shopify Transaction ID",
        index=True,
        help="Shopify payment transaction id for idempotent payment registration.",
    )
