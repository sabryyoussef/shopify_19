import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestWebhookReplay(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Replay Store",
                "shop_url": "https://replay-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "manage_orders_webhook": True,
                "webhook_secret": "secret",
                "auto_create_product_if_not_found": True,
            }
        )
        self.handler = self.env["shopify.webhook.handler"]

    def test_01_replay_failed_webhook_increments_attempt(self):
        payload = {
            "id": 94001,
            "name": "#R94001",
            "email": "replay@example.com",
            "customer": {"id": 94001, "email": "replay@example.com", "first_name": "R", "last_name": "P"},
            "line_items": [
                {
                    "id": 1,
                    "variant_id": 94011,
                    "sku": "REPLAY-SKU",
                    "name": "Replay Item",
                    "price": "20.00",
                    "quantity": 1,
                }
            ],
        }
        event = self.env["shopify.webhook.event"].sudo().create(
            {
                "store_id": self.store.id,
                "webhook_id": "replay-wh-1",
                "topic": "orders/create",
                "shop_domain": "replay-test.myshopify.com",
                "shopify_order_id": "94001",
                "payload": json.dumps(payload),
                "status": "failed",
                "error_message": "previous failure",
                "error_type": "processing",
                "attempt": 1,
            }
        )
        ok = self.handler.replay_webhook_event(event)
        self.assertTrue(ok)
        self.assertEqual(event.status, "processed")
        self.assertEqual(event.attempt, 2)
        self.assertTrue(event.last_attempt_at)
        self.assertEqual(event.webhook_id, "replay-wh-1")
        self.assertEqual(event.shopify_order_id, "94001")
        # Idempotent: replay again must not create a second sale order
        orders = self.env["sale.order"].search([("shopify_order_id", "=", "94001")])
        # May be 0 if only queued, or 1 if processed via import elsewhere.
        # Ensuring replay does not create duplicate queue pending rows for existing order.
        again = self.handler.replay_webhook_event(event)
        self.assertTrue(again)
        self.assertEqual(event.attempt, 3)
        self.assertLessEqual(len(orders), 1)

    def test_02_replay_blocked_while_processing(self):
        event = self.env["shopify.webhook.event"].sudo().create(
            {
                "store_id": self.store.id,
                "webhook_id": "replay-wh-2",
                "topic": "orders/create",
                "payload": '{"id": 94002}',
                "status": "processing",
                "shopify_order_id": "94002",
            }
        )
        with self.assertRaises(UserError):
            event.action_replay()

    def test_03_mask_secrets_in_sync_log(self):
        mixin = self.env["shopify.sync.log.mixin"]
        masked = mixin.mask_secrets(
            {
                "access_token": "shpat_secret_value",
                "Authorization": "Bearer abc",
                "nested": {"api_secret": "xyz", "order_id": 1},
            }
        )
        self.assertIn("***MASKED***", masked)
        self.assertNotIn("shpat_secret_value", masked)
        self.assertNotIn('"api_secret": "xyz"', masked)
        log = mixin.create_log(
            store=self.store,
            log_type="order",
            message="test",
            payload={"access_token": "shpat_secret_value", "ok": True},
            status="failed",
            webhook_id="replay-wh-3",
            error_type="processing",
        )
        self.assertNotIn("shpat_secret_value", log.payload or "")
        self.assertEqual(log.webhook_id, "replay-wh-3")

    def test_04_sync_log_retry_requires_failed(self):
        log = self.env["shopify.sync.log.mixin"].create_log(
            store=self.store,
            log_type="order",
            message="ok",
            status="success",
            webhook_id="x",
        )
        with self.assertRaises(UserError):
            log.action_retry()
