# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ShopifyExportWizard(models.TransientModel):
    _name = "shopify.export.wizard"
    _description = "Export Products to Shopify"

    store_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
    )
    product_tmpl_ids = fields.Many2many(
        "product.template",
        string="Products",
        relation="shopify_export_wizard_product_tmpl_rel",
        column1="wizard_id",
        column2="product_tmpl_id",
        help="Products to export.",
    )

    # 1. Set Basic Details
    export_name = fields.Boolean(string="Export Product Name", default=True)
    export_description = fields.Boolean(string="Export Product Description", default=True)
    export_tags = fields.Boolean(string="Export Product Tags", default=True)
    export_categories = fields.Boolean(string="Export Product Categories", default=True)

    # 2. Set Price
    export_price = fields.Boolean(
        string="Export Price from Pricelist",
        default=True,
        help="Export product price from the configured Shopify pricelist.",
    )

    # 3. Set Image
    export_image = fields.Boolean(string="Export Product Image", default=True)

    # 4. Publish in Website
    publish_option = fields.Selection(
        [
            ("web_only", "Web Only"),
            ("web_pos", "Web + POS"),
            ("not_published", "Unpublish"),
        ],
        string="Publish Option",
        required=True,
        default="web_only",
    )

    # 5. Force Export (recreate if deleted on Shopify)
    force_export = fields.Boolean(
        string="Force Export",
        default=False,
        help="Force re-export even if product hasn't changed. Use when products were deleted on Shopify.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids") or []
        if active_model == "product.template" and active_ids:
            res["product_tmpl_ids"] = [(6, 0, active_ids)]
        elif active_model == "shopify.product.layer" and active_ids:
            layers = self.env["shopify.product.layer"].browse(active_ids).exists()
            product_ids = layers.mapped("product_tmpl_id").ids
            res["product_tmpl_ids"] = [(6, 0, product_ids)]
            if layers and "store_id" in fields_list and not res.get("store_id"):
                res["store_id"] = layers[0].store_id.id
        if self.env.context.get("default_store_id") and "store_id" in fields_list:
            res["store_id"] = self.env.context["default_store_id"]
        return res

    def action_export(self):
        self.ensure_one()
        if not self.product_tmpl_ids:
            raise UserError(_("Please select at least one product."))
        warning_msgs = []
        if self.export_price and not (self.store_id.shopify_pricelist_id or self.store_id.pricelist_id):
            # For "export now" flows, default to list price instead of blocking the export.
            self.export_price = False
            warning_msgs.append(
                _(
                    "Shopify store '%s' has no pricelist configured. "
                    "Prices were exported from the product sales price instead."
                )
                % self.store_id.name
            )
        Layer = self.env["shopify.product.layer"]
        from ..services.product_service import ProductService

        service = ProductService(self.env)
        exported = 0
        errors = []
        skipped = []
        for product in self.product_tmpl_ids:
            if product.type == "service":
                skipped.append(product.display_name or product.name)
                continue
            layer = Layer.search(
                [
                    ("product_tmpl_id", "=", product.id),
                    ("store_id", "=", self.store_id.id),
                ],
                limit=1,
            )
            # If force export is enabled, clear checksum to force re-export
            if self.force_export and layer:
                layer.sudo().write({'checksum': False})
                # Also clear the product_map checksum which is actually checked
                ProductMap = self.env['shopify.product.map']
                product_map = ProductMap.search([
                    ('product_tmpl_id', '=', product.id),
                    ('store_id', '=', self.store_id.id),
                ], limit=1)
                if product_map:
                    product_map.sudo().write({'checksum': False})
            if not layer:
                layer = Layer.create({
                    "product_tmpl_id": product.id,
                    "store_id": self.store_id.id,
                    "state": "draft",
                })
            try:
                # Log export options for debugging
                _logger.info("Starting export for product %s with options: name=%s, description=%s, tags=%s, categories=%s, price=%s, image=%s, publish=%s", 
                           product.name, self.export_name, self.export_description, self.export_tags, 
                           self.export_categories, self.export_price, self.export_image, self.publish_option)
                
                result = service.export_product_to_shopify(
                    product,
                    self.store_id,
                    layer=layer,
                    export_name=self.export_name,
                    export_description=self.export_description,
                    export_tags=self.export_tags,
                    export_categories=self.export_categories,
                    export_price=self.export_price,
                    export_image=self.export_image,
                    publish_option=self.publish_option,
                )
                _logger.info("Successfully exported product %s to Shopify with ID %s", product.name, result.get("shopify_product_id"))
                layer.write({
                    "shopify_product_id": result["shopify_product_id"],
                    "state": "exported",
                    "publish_status": self.publish_option,
                })
                exported += 1
            except Exception as e:
                _logger.error("Failed to export product %s: %s", product.name, str(e))
                errors.append("%s: %s" % (product.name, str(e)))
        if errors and exported == 0:
            raise UserError(
                _("Export failed for all products:\n%s") % "\n".join(errors[:15])
            )
        msg = (
            _("Successfully exported %s product(s) to Shopify.") % exported
            if not errors
            else _("Exported %s product(s). %s failed:\n%s")
            % (exported, len(errors), "\n".join(errors[:10]))
        )
        if skipped:
            msg = "%s\n\n%s" % (
                msg,
                _("Skipped %s service product(s).") % len(skipped),
            )
        if warning_msgs:
            msg = "%s\n\n%s" % (msg, "\n".join(warning_msgs[:5]))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Export to Shopify"),
                "message": msg,
                "type": "warning" if errors else "success",
                "sticky": bool(errors),
            },
        }


class ShopifyExportStockWizard(models.TransientModel):
    _name = "shopify.export.stock.wizard"
    _description = "Export Stock to Shopify"

    store_id = fields.Many2one(
        "shopify.store",
        string="Store",
        required=True,
    )
    export_stock_from = fields.Datetime(
        string="Export Stock From",
        help="Only products with stock movements after this date will be exported.",
    )
    product_ids = fields.Many2many(
        "product.product",
        string="Products",
        help="If set, restrict stock export to these products.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context or {}
        active_model = ctx.get("active_model")
        active_ids = ctx.get("active_ids") or []

        if active_model == "product.product" and active_ids and "product_ids" in fields_list:
            res["product_ids"] = [(6, 0, active_ids)]

        if ctx.get("default_store_id") and "store_id" in fields_list:
            res["store_id"] = ctx["default_store_id"]

        return res

    def action_export_stock(self):
        self.ensure_one()
        stock_service = self.env["shopify.stock.service"]
        products = self.product_ids if self.product_ids else None
        queue = stock_service.export_stock(
            store=self.store_id,
            export_from=self.export_stock_from,
            products=products,
            operation_type="manual",
        )
        if not queue:
            message = _("No products found for stock export.")
        else:
            message = _(
                "Stock export queue %(queue)s created with %(count)s products."
            ) % {
                "queue": queue.name,
                "count": queue.products_to_process,
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Export Stock"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

