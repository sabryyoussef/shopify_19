from odoo import api, models, _

from ..services.queue_service import ProductQueueService
from .license_mixin import license_is_active_strict
from .sync_log import ShopifySyncLogMixin


class ShopifyProductSync(ShopifySyncLogMixin):
    _name = "shopify.product.sync"
    _description = "Shopify Product Synchronization"

    @api.model
    def sync_products(self, store):
        service = ProductQueueService(self.env)
        try:
            queue = service.enqueue_products_by_date(
                store=store,
                import_based_on="update_date",
                from_date=False,
                to_date=False,
            )
            service.process_queue(queue)
        except Exception as e:
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="product",
                message=str(e),
                payload=False,
                status="failed",
            )
            return

        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="product",
            message=_("Products synchronized from Shopify via product queue."),
            payload=False,
            status="success",
        )

    @api.model
    def cron_sync_products(self):
        if not license_is_active_strict(self.env):
            _logger = __import__("logging").getLogger(__name__)
            _logger.warning("License inactive - cron skipped (product sync)")
            return
        stores = self.env["shopify.store"].search([("active", "=", True)])
        for store in stores:
            self.sync_products(store)

