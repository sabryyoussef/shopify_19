# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ShopifyProductLayer(models.Model):
    _name = "shopify.product.layer"
    _description = "Shopify Product Export Layer"
    _rec_name = "display_name"

    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Odoo Product",
        required=True,
        ondelete="cascade",
    )
    store_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
        ondelete="cascade",
    )
    shopify_product_id = fields.Char(
        string="Shopify Product ID",
        readonly=True,
        index=True,
        copy=False,
    )
    state = fields.Selection(
        [
            ("draft", "Prepared (Not Exported)"),
            ("exported", "Exported"),
        ],
        string="Export Status",
        default="draft",
        required=True,
        readonly=True,
    )
    publish_status = fields.Selection(
        [
            ("web_only", "Published in Web Only"),
            ("web_pos", "Published in Web + POS"),
            ("not_published", "Do Not Publish"),
        ],
        string="Publish Status",
        readonly=True,
        copy=False,
    )
    # Optional overrides for editing before export
    description_override = fields.Html(
        string="Description Override",
        help="Override product description for Shopify. Leave empty to use product description.",
    )

    # Checksum for change detection
    checksum = fields.Char(
        string="Checksum",
        index=True,
        help="Checksum of product data to detect changes for export.",
    )

    display_name = fields.Char(compute="_compute_display_name", store=True)

    _sql_constraints = [
        (
            "product_store_uniq",
            "UNIQUE(product_tmpl_id, store_id)",
            "This product is already prepared for this store.",
        )
    ]

    @api.depends("product_tmpl_id", "store_id", "shopify_product_id")
    def _compute_display_name(self):
        for rec in self:
            name = rec.product_tmpl_id.name or _("Product")
            if rec.store_id:
                name = "%s @ %s" % (name, rec.store_id.name)
            if rec.shopify_product_id:
                name = "%s [%s]" % (name, rec.shopify_product_id)
            rec.display_name = name

    def action_export_to_shopify(self):
        """Open export wizard in context of this layer's product and store."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_export_wizard"
        )
        action["context"] = {
            "default_store_id": self.store_id.id,
            "default_product_tmpl_ids": [(6, 0, self.product_tmpl_id.ids)],
            "active_model": "shopify.product.layer",
            "active_ids": self.ids,
        }
        return action
