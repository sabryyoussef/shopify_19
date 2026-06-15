# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ShopifyPrepareExportWizard(models.TransientModel):
    _name = "shopify.prepare.export.wizard"
    _description = "Shopify Prepare Product for Export"

    store_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get("active_ids") or []
        if not active_ids and "store_id" in fields_list:
            store_id = self.env.context.get("default_store_id")
            if store_id:
                res["store_id"] = store_id
        return res

    def action_prepare(self):
        self.ensure_one()
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids") or []
        if active_model != "product.template":
            raise UserError(
                _("This action must be run from the Products list (Sales → Products → Products). Select one or more products first.")
            )
        if not active_ids:
            raise UserError(_("Please select one or more products."))
        ProductTemplate = self.env["product.template"]
        Layer = self.env["shopify.product.layer"]
        products = ProductTemplate.browse(active_ids).exists()
        created = 0
        updated = 0
        for product in products:
            layer = Layer.search(
                [
                    ("product_tmpl_id", "=", product.id),
                    ("store_id", "=", self.store_id.id),
                ],
                limit=1,
            )
            if layer:
                updated += 1
            else:
                Layer.create({
                    "product_tmpl_id": product.id,
                    "store_id": self.store_id.id,
                    "state": "draft",
                })
                created += 1
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_product_layer"
        )
        action["domain"] = [
            ("store_id", "=", self.store_id.id),
            ("product_tmpl_id", "in", products.ids),
        ]
        action["context"] = {
            "default_store_id": self.store_id.id,
            "search_default_draft": 1,
        }
        return action
