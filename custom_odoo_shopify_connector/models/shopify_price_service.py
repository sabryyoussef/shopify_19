from odoo import _, api, fields, models

from .license_mixin import license_is_active_strict, trial_batch_limit


class ShopifyPriceUpdateQueue(models.Model):
    _name = "shopify.price.update.queue"
    _description = "Shopify Price Update Queue"
    _order = "create_date desc"

    name = fields.Char(
        string="Queue Reference",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )
    store_id = fields.Many2one(
        "shopify.store",
        string="Store",
        required=True,
        ondelete="cascade",
    )
    operation_type = fields.Selection(
        [
            ("manual", "Manual"),
            ("scheduler", "Scheduler"),
        ],
        string="Operation Type",
        required=True,
        default="manual",
    )
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="Status",
        default="pending",
        required=True,
    )
    products_to_process = fields.Integer(string="Products To Process")
    processed_products = fields.Integer(string="Processed Products")
    error_message = fields.Text(string="Error Message")

    line_ids = fields.One2many(
        "shopify.price.update.queue.line",
        "queue_id",
        string="Price Lines",
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shopify.price.update.queue"
                ) or _("New")
        return super().create(vals_list)

    def _process_queue(self, batch_size=100):
        """Process pending price lines in batches."""
        service = self.env["shopify.price.service"]
        for queue in self:
            service.process_queue(queue, batch_size=batch_size)

    @api.model
    def cron_process_pending_queues(self, batch_size=100):
        """Cron entry point: process all pending price update queues in small batches."""
        if not license_is_active_strict(self.env):
            import logging

            logging.getLogger(__name__).warning(
                "License inactive - cron skipped (price queues)"
            )
            return
        queues = self.search([("status", "in", ["pending", "processing"])], limit=20)
        for queue in queues:
            queue._process_queue(batch_size=batch_size)


class ShopifyPriceUpdateQueueLine(models.Model):
    _name = "shopify.price.update.queue.line"
    _description = "Shopify Price Update Queue Line"

    queue_id = fields.Many2one(
        "shopify.price.update.queue",
        string="Queue",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        ondelete="cascade",
    )
    target_price = fields.Float(string="Target Price")
    last_synced_price = fields.Float(string="Last Synced Price")
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ],
        string="Status",
        default="pending",
        required=True,
    )
    message = fields.Text(string="Message")


class ShopifyPriceService(models.AbstractModel):
    _name = "shopify.price.service"
    _description = "Shopify Price Service"

    @api.model
    def _get_pricelist_for_store(self, store):
        """Return the pricelist to use for price synchronization."""
        return store.shopify_pricelist_id or store.pricelist_id

    def _compute_price(self, store, product):
        """Compute product price for Shopify using the store's configured pricelist."""
        pricelist = self._get_pricelist_for_store(store)
        if pricelist:
            price = pricelist._get_product_price(product, 1.0)
        else:
            price = product.lst_price or product.list_price
        return float(price or 0.0)

    def process_queue(self, queue, batch_size=100):
        """Process a single price queue with basic duplicate protection and logging."""
        VariantMap = self.env["shopify.variant.map"]
        Log = self.env["shopify.sync.log"].sudo()

        store = queue.store_id
        api_client = store._get_api_client()

        queue.status = "processing"
        license_ok = license_is_active_strict(self.env)
        if not license_ok and batch_size:
            batch_size = min(batch_size, trial_batch_limit() + 1)
        pending_lines = queue.line_ids.filtered(lambda l: l.state == "pending")[:batch_size]

        for line in pending_lines:
            if not license_ok:
                __import__("time").sleep(1)
            product = line.product_id

            # Resolve Shopify variant mapping
            mapping = VariantMap.search(
                [("store_id", "=", store.id), ("product_id", "=", product.id)],
                limit=1,
            )
            if not mapping or not mapping.shopify_variant_id:
                line.state = "failed"
                msg = _(
                    "Missing Shopify variant mapping for product %s (store %s)."
                ) % (product.display_name, store.name)
                line.message = msg
                Log.create(
                    {
                        "store_id": store.id,
                        "product_id": product.id,
                        "operation": "product",
                        "message": msg,
                        "status": "failed",
                    }
                )
                continue

            try:
                target_price = self._compute_price(store, product)
                line.target_price = target_price
                price_str = "%.2f" % target_price

                # Duplicate protection: if last_synced_price on mapping matches, skip API call
                if (
                    getattr(mapping, "last_synced_price", False) is not False
                    and mapping.last_synced_price == target_price
                ):
                    line.state = "skipped"
                    line.message = _(
                        "Skipped price update for %s; Shopify price already matches Odoo price."
                    ) % product.display_name
                    Log.create(
                        {
                            "store_id": store.id,
                            "product_id": product.id,
                            "operation": "product",
                            "message": line.message,
                            "status": "success",
                        }
                    )
                    continue

                payload = {
                    "variant": {
                        "id": mapping.shopify_variant_id,
                        "price": price_str,
                    }
                }
                api_client.update_variant(mapping.shopify_variant_id, payload)

                # Update tracking and log success
                mapping.last_synced_price = target_price
                line.last_synced_price = target_price
                line.state = "done"
                line.message = _(
                    "Updated Shopify price for %(product)s to %(price)s."
                ) % {"product": product.display_name, "price": price_str}

                Log.create(
                    {
                        "store_id": store.id,
                        "product_id": product.id,
                        "operation": "product",
                        "message": line.message,
                        "status": "success",
                    }
                )
            except Exception as e:
                line.state = "failed"
                line.message = str(e)
                queue.error_message = (queue.error_message or "") + "\n" + str(e)
                Log.create(
                    {
                        "store_id": store.id,
                        "product_id": product.id,
                        "operation": "product",
                        "message": "Price update failed for %s: %s"
                        % (product.display_name, str(e)),
                        "status": "failed",
                    }
                )

        queue.processed_products = len(
            queue.line_ids.filtered(lambda l: l.state in ("done", "failed", "skipped"))
        )

        if all(l.state in ("done", "skipped") for l in queue.line_ids):
            queue.status = "done"
        elif any(l.state == "failed" for l in queue.line_ids):
            queue.status = "failed"
        else:
            queue.status = "processing"

