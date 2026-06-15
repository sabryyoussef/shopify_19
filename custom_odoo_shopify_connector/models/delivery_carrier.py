# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    shopify_delivery_source_code = fields.Char(
        string="Shopify Delivery Source Code",
        help="Source code used when mapping this carrier to Shopify.",
    )
    shopify_delivery_code = fields.Char(
        string="Shopify Delivery Code",
        help="Code used by Shopify for this delivery method.",
    )
    shopify_tracking_company = fields.Char(
        string="Tracking Company",
        help="Tracking company name sent to Shopify fulfillment API (e.g. DHL, FedEx, UPS).",
    )
