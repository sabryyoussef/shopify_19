from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ShopifyProductSyncCheckLine(models.TransientModel):
    _name = "shopify.product.sync.check.line"
    _description = "Product Sync Check Line (Odoo vs Shopify)"

    check_id = fields.Many2one(
        "shopify.product.sync.check",
        string="Sync Check",
        ondelete="cascade",
        required=True,
    )
    shopify_product_id = fields.Char(string="Shopify Product ID", readonly=True)
    shopify_title = fields.Char(string="Shopify Title", readonly=True)
    shopify_sku = fields.Char(string="Shopify SKU", readonly=True)
    shopify_price = fields.Float(string="Shopify Price", readonly=True)
    in_odoo = fields.Boolean(string="In Odoo", readonly=True)
    in_shopify = fields.Boolean(string="In Shopify", readonly=True)
    odoo_product_id = fields.Many2one(
        "product.template",
        string="Odoo Product",
        readonly=True,
    )
    status = fields.Selection(
        [
            ("synced", "Synced"),
            ("only_in_odoo", "Only in Odoo"),
            ("only_in_shopify", "Only in Shopify"),
        ],
        string="Status",
        compute="_compute_status",
        store=True,
        readonly=True,
    )

    @api.depends("in_odoo", "in_shopify")
    def _compute_status(self):
        for line in self:
            if line.in_odoo and line.in_shopify:
                line.status = "synced"
            elif line.in_odoo:
                line.status = "only_in_odoo"
            else:
                line.status = "only_in_shopify"


class ShopifyProductSyncCheck(models.TransientModel):
    _name = "shopify.product.sync.check"
    _description = "Product Sync Check (Compare Odoo vs Shopify)"

    store_id = fields.Many2one(
        "shopify.store",
        string="Store",
        required=True,
    )
    line_ids = fields.One2many(
        "shopify.product.sync.check.line",
        "check_id",
        string="Comparison Lines",
        readonly=True,
    )
    count_synced = fields.Integer(string="Synced", compute="_compute_counts", store=False)
    count_only_odoo = fields.Integer(string="Only in Odoo", compute="_compute_counts", store=False)
    count_only_shopify = fields.Integer(string="Only in Shopify", compute="_compute_counts", store=False)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get("default_store_id") or self.env.context.get("active_id")
        if active_id and "store_id" in fields_list:
            res["store_id"] = active_id
        return res

    def _compute_counts(self):
        for rec in self:
            lines = rec.line_ids
            rec.count_synced = len(lines.filtered(lambda l: l.status == "synced"))
            rec.count_only_odoo = len(lines.filtered(lambda l: l.status == "only_in_odoo"))
            rec.count_only_shopify = len(lines.filtered(lambda l: l.status == "only_in_shopify"))

    def action_load_comparison(self):
        """Fetch products from Shopify and Odoo (via product map) and build comparison lines."""
        self.ensure_one()
        if not self.store_id:
            raise UserError(_("Please select a store."))

        api_client = self.store_id._get_api_client()
        try:
            shopify_products = api_client.get_products(limit=250)
        except Exception as e:
            raise UserError(_("Failed to fetch products from Shopify: %s") % e)

        ProductMap = self.env["shopify.product.map"]
        maps = ProductMap.search([("store_id", "=", self.store_id.id)])
        odoo_by_shopify_id = {m.shopify_product_id: m for m in maps}

        shopify_by_id = {str(p.get("id")): p for p in shopify_products if p.get("id")}
        all_shopify_ids = set(shopify_by_id.keys()) | set(odoo_by_shopify_id.keys())

        Line = self.env["shopify.product.sync.check.line"]
        self.line_ids.unlink()

        lines_vals = []
        for shopify_id in sorted(all_shopify_ids, key=lambda x: int(x) if x.isdigit() else 0):
            sp = shopify_by_id.get(shopify_id)
            m = odoo_by_shopify_id.get(shopify_id)
            in_shopify = bool(sp)
            in_odoo = bool(m)

            title = ""
            sku = ""
            price = 0.0
            if sp:
                title = sp.get("title") or ""
                variants = sp.get("variants") or []
                if variants:
                    sku = variants[0].get("sku") or ""
                    price = float(variants[0].get("price") or 0)

            lines_vals.append(
                {
                    "check_id": self.id,
                    "shopify_product_id": shopify_id,
                    "shopify_title": title,
                    "shopify_sku": sku,
                    "shopify_price": price,
                    "in_odoo": in_odoo,
                    "in_shopify": in_shopify,
                    "odoo_product_id": m.product_tmpl_id.id if m else False,
                }
            )

        Line.create(lines_vals)
        return True
