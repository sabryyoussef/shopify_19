from odoo import fields, models


class ShopifyLocation(models.Model):
    _name = "shopify.location"
    _description = "Shopify Location"

    name = fields.Char(string="Location Name", required=True)
    shopify_location_id = fields.Char(
        string="Shopify Location ID",
        required=True,
        index=True,
        help="ID of the location in Shopify.",
    )
    shopify_store_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
        ondelete="cascade",
    )
    active = fields.Boolean(default=True)

    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse",
        help="Primary warehouse represented by this Shopify location.",
    )
    order_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse for Orders",
        help="Warehouse to use when importing orders for this Shopify location.",
    )
    import_stock_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse for Stock Import",
        help="Warehouse that will receive stock when importing inventory from Shopify.",
    )
    export_stock_warehouse_ids = fields.Many2many(
        "stock.warehouse",
        "shopify_location_export_warehouse_rel",
        "location_id",
        "warehouse_id",
        string="Warehouses for Stock Export",
        help="Warehouses whose stock will be summed and exported to this Shopify location.",
    )

    _sql_constraints = [
        (
            "shopify_location_unique",
            "unique(shopify_location_id, shopify_store_id)",
            "The Shopify Location ID must be unique per Shopify store.",
        )
    ]

