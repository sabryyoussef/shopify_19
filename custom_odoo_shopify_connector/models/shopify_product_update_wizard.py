from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ShopifyUpdateProductWizard(models.TransientModel):
    _name = "shopify.update.product.wizard"
    _description = "Shopify Update Product Wizard"

    store_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        required=True,
    )
    product_ids = fields.Many2many(
        "product.product",
        string="Products",
        help="Product variants whose data should be updated on Shopify.",
    )

    update_price = fields.Boolean(string="Update Price", default=True)
    update_inventory = fields.Boolean(string="Update Inventory")
    update_description = fields.Boolean(string="Update Description")
    update_image = fields.Boolean(string="Update Product Image")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context or {}
        active_model = ctx.get("active_model")
        active_ids = ctx.get("active_ids") or []

        products = self.env["product.product"]
        if active_model == "product.product" and active_ids:
            products = self.env["product.product"].browse(active_ids)
        elif active_model == "product.template" and active_ids:
            templates = self.env["product.template"].browse(active_ids)
            products = templates.mapped("product_variant_ids")
        elif active_model == "shopify.product.layer" and active_ids:
            layers = self.env["shopify.product.layer"].browse(active_ids).exists()
            templates = layers.mapped("product_tmpl_id")
            products = templates.mapped("product_variant_ids")
            # Pre-fill store from first layer when possible
            if layers and "store_id" in fields_list and not res.get("store_id"):
                res["store_id"] = layers[0].store_id.id

        if products and "product_ids" in fields_list:
            res["product_ids"] = [(6, 0, products.ids)]

        if ctx.get("default_store_id") and "store_id" in fields_list and not res.get("store_id"):
            res["store_id"] = ctx["default_store_id"]

        return res

    def action_update_products(self):
        self.ensure_one()
        if not (
            self.update_price
            or self.update_inventory
            or self.update_description
            or self.update_image
        ):
            raise UserError(_("Please select at least one option to update."))
        if not self.product_ids:
            raise UserError(_("Please select at least one product."))

        # For now, only price and image updates are implemented.
        if self.update_inventory or self.update_description:
            raise UserError(
                _(
                    "Only price and product image updates are implemented in this version. "
                    "Please enable 'Update Price' and/or 'Update Product Image' to continue."
                )
            )

        notifications = []

        if self.update_price:
            price_queue_model = self.env["shopify.price.update.queue"]
            price_queue = price_queue_model.create(
                {
                    "store_id": self.store_id.id,
                    "operation_type": "manual",
                    "status": "pending",
                    "products_to_process": len(self.product_ids),
                }
            )

            price_line_model = self.env["shopify.price.update.queue.line"]
            for product in self.product_ids:
                price_line_model.create(
                    {
                        "queue_id": price_queue.id,
                        "product_id": product.id,
                    }
                )

            # Process synchronously for manual operation (batch-limited for safety).
            price_queue._process_queue(batch_size=100)
            notifications.append(
                _(
                    "Price update queue %(queue)s processed. Products: %(count)s."
                )
                % {
                    "queue": price_queue.name,
                    "count": price_queue.products_to_process,
                }
            )

        if self.update_image:
            image_queue_model = self.env["shopify.image.update.queue"]
            image_queue = image_queue_model.create(
                {
                    "store_id": self.store_id.id,
                    "operation_type": "manual",
                    "status": "pending",
                    "products_to_process": len(self.product_ids),
                }
            )

            image_line_model = self.env["shopify.image.update.queue.line"]
            for product in self.product_ids:
                image_line_model.create(
                    {
                        "queue_id": image_queue.id,
                        "product_id": product.id,
                    }
                )

            # Process synchronously for manual operation; service is batch-limited
            # for safety and rate-limit protection.
            image_queue._process_queue(batch_size=50)
            notifications.append(
                _(
                    "Image update queue %(queue)s processed. Products: %(count)s."
                )
                % {
                    "queue": image_queue.name,
                    "count": image_queue.products_to_process,
                }
            )

        message = "\n".join(notifications)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Update Product"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

