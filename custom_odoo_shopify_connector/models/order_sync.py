import json

from odoo import api, models, _
from ..services.order_import_service import OrderImportService


class ShopifyOrderSync(models.AbstractModel):
    _name = "shopify.order.sync"
    _description = "Shopify Order Synchronization"

    @api.model
    def sync_orders(self, store):
        """Manual order sync entry point.

        IMPORTANT: this method must NOT create sale orders directly.
        It only fetches orders from Shopify and enqueues them so that the
        dedicated queue worker (shopify.order.queue) performs the actual
        import using the standard workflow.
        """
        api_client = store._get_api_client()

        try:
            orders = api_client.get_orders()
        except Exception as e:
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="order",
                message=str(e),
                payload=False,
                status="failed",
            )
            return

        import_service = OrderImportService(self.env)
        Queue = self.env["shopify.order.queue"]

        for order in orders:
            try:
                if not import_service.should_import_order(store, order):
                    continue

                shopify_order_id = order.get("id")

                # Avoid creating multiple pending/processing queue records
                # for the same Shopify order and store.
                if shopify_order_id:
                    existing = Queue.search(
                        [
                            ("store_id", "=", store.id),
                            ("shopify_order_id", "=", str(shopify_order_id)),
                            ("state", "in", ["pending", "processing"]),
                        ],
                        limit=1,
                    )
                    if existing:
                        continue

                Queue.create(
                    {
                        "store_id": store.id,
                        "shopify_order_id": str(shopify_order_id or ""),
                        "payload": json.dumps(order),
                        "state": "pending",
                        "job_type": "order",
                    }
                )
            except Exception as e:
                self.env["shopify.sync.log.mixin"].create_log(
                    store=store,
                    log_type="order",
                    message=str(e),
                    payload=order,
                    status="failed",
                )

        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message=_("Orders enqueued from manual sync."),
            payload=False,
            status="success",
        )

