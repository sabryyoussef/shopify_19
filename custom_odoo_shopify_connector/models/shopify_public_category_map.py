# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ShopifyPublicCategoryMap(models.Model):
    """Permanent mapping: Odoo website public category ↔ Shopify custom collection."""

    _name = "shopify.public.category.map"
    _description = "Shopify Public Category Collection Map"
    _order = "store_id, public_categ_id"

    store_id = fields.Many2one(
        "shopify.store",
        required=True,
        ondelete="cascade",
        index=True,
    )
    public_categ_id = fields.Many2one(
        "product.public.category",
        string="Website Category",
        required=True,
        ondelete="cascade",
        index=True,
    )
    public_categ_name = fields.Char(
        related="public_categ_id.name",
        store=True,
        readonly=True,
    )
    parent_categ_id = fields.Many2one(
        related="public_categ_id.parent_id",
        store=True,
        readonly=True,
    )
    shopify_collection_id = fields.Char(
        string="Shopify Collection ID",
        index=True,
        help="Permanent Shopify custom collection ID. Never adopt by title alone.",
    )
    shopify_handle = fields.Char(string="Shopify Handle", index=True)
    collection_type = fields.Selection(
        [("custom", "Custom (manual)")],
        default="custom",
        required=True,
    )
    published_on_shopify = fields.Boolean(
        string="Published on Online Store",
        default=False,
        help="Phase 2 keeps this False until live cutover is approved.",
    )
    nav_eligible = fields.Boolean(
        string="Eligible for Shop Menu",
        default=False,
        help="True when the collection has at least one mapped published Shopify product.",
    )
    product_count_odoo = fields.Integer(
        string="Odoo Products",
        compute="_compute_counts",
    )
    product_count_mapped = fields.Integer(
        string="Mapped to Shopify",
        compute="_compute_counts",
    )
    last_sync_at = fields.Datetime()
    sync_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Pending"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("conflict", "Conflict — review"),
        ],
        default="draft",
        required=True,
        index=True,
    )
    sync_error = fields.Text()
    last_dry_run_json = fields.Text()

    _sql_constraints = [
        (
            "uniq_public_categ_per_store",
            "unique(store_id, public_categ_id)",
            "Each website category can map only once per Shopify store.",
        ),
        (
            "uniq_shopify_collection_per_store",
            "unique(store_id, shopify_collection_id)",
            "Each Shopify collection can map only once per store.",
        ),
    ]

    @api.depends("public_categ_id", "store_id")
    def _compute_counts(self):
        Product = self.env["product.template"]
        ProductMap = self.env["shopify.product.map"]
        for rec in self:
            if not rec.public_categ_id:
                rec.product_count_odoo = 0
                rec.product_count_mapped = 0
                continue
            tmpl_ids = Product.search([("public_categ_ids", "in", [rec.public_categ_id.id])]).ids
            rec.product_count_odoo = len(tmpl_ids)
            if not tmpl_ids or not rec.store_id:
                rec.product_count_mapped = 0
                continue
            rec.product_count_mapped = ProductMap.search_count(
                [
                    ("store_id", "=", rec.store_id.id),
                    ("product_tmpl_id", "in", tmpl_ids),
                    ("shopify_product_id", "!=", False),
                ]
            )

    def action_sync_one(self):
        self.ensure_one()
        self.env["shopify.public.category.service"].sync_category(
            self.store_id, self.public_categ_id, dry_run=False
        )
        return True

    def action_dry_run_one(self):
        self.ensure_one()
        self.env["shopify.public.category.service"].sync_category(
            self.store_id, self.public_categ_id, dry_run=True
        )
        return True
