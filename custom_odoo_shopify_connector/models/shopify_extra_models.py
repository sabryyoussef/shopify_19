from odoo import fields, models, _


SH_STATE_SELECTION = [
    ("draft", "Draft"),
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("done", "Done"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]


class ShopifyProductTag(models.Model):
    _name = "shopify.product.tag"
    _description = "Shopify Product Tag"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="draft",
    )
    description = fields.Text()


class ShopifyProductCollection(models.Model):
    _name = "shopify.product.collection"
    _description = "Shopify Product Collection"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="draft",
    )
    description = fields.Text()


class ShopifyCustomerMapping(models.Model):
    _name = "shopify.customer.mapping"
    _description = "Shopify Customer Mapping"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify Customer ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Odoo Customer",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="done",
    )
    message = fields.Text()


class ShopifyInventoryAdjustmentLog(models.Model):
    _name = "shopify.inventory.adjustment.log"
    _description = "Shopify Inventory Adjustment Log"

    name = fields.Char(required=True, default=lambda self: _("New"))
    shopify_id = fields.Char(string="Shopify Inventory ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="done",
    )
    message = fields.Text()


class ShopifyCarrierMapping(models.Model):
    _name = "shopify.carrier.mapping"
    _description = "Shopify Carrier Mapping"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify Carrier ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    carrier_id = fields.Many2one(
        "delivery.carrier",
        string="Odoo Delivery Method",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="done",
    )
    message = fields.Text()


class ShopifyCustomerQueue(models.Model):
    _name = "shopify.customer.queue"
    _description = "Shopify Customer Queue"
    _order = "create_date desc"

    name = fields.Char(required=True, default=lambda self: _("New"))
    shopify_id = fields.Char(string="Shopify Customer ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="pending",
    )
    message = fields.Text()


class ShopifyApiLog(models.Model):
    _name = "shopify.api.log"
    _description = "Shopify API Log"
    _order = "create_date desc"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify Resource ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    endpoint = fields.Char(string="Endpoint")
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="done",
    )
    message = fields.Text()


class ShopifyErrorLog(models.Model):
    _name = "shopify.error.log"
    _description = "Shopify Error Log"
    _order = "create_date desc"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify Resource ID", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="failed",
    )
    message = fields.Text()


class ShopifySyncReport(models.Model):
    _name = "shopify.sync.report"
    _description = "Shopify Synchronization Report"

    name = fields.Char(required=True)
    shopify_id = fields.Char(string="Shopify Reference", index=True)
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        ondelete="cascade",
    )
    state = fields.Selection(
        SH_STATE_SELECTION,
        string="State",
        default="done",
    )
    description = fields.Text()

