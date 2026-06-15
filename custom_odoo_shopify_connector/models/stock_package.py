# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class StockPackage(models.Model):
    _inherit = "stock.package"

    carrier_id = fields.Many2one(
        "delivery.carrier",
        string="Carrier",
        help="Carrier used for this package (for Shopify fulfillment sync).",
    )
    carrier_tracking_ref = fields.Char(
        string="Tracking Reference",
        copy=False,
        help="Tracking number for this package (sent to Shopify when multiple packages).",
    )
