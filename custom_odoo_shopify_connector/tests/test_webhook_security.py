import base64
import hashlib
import hmac

from odoo.tests.common import TransactionCase


class TestWebhookSecurity(TransactionCase):
    def _hmac(self, secret, raw_data):
        digest = hmac.new(secret.encode("utf-8"), raw_data, hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def test_validate_webhook_request_requires_hmac_and_id(self):
        Store = self.env["shopify.store"].sudo()
        handler = self.env["shopify.webhook.handler"].sudo()

        store = Store.create(
            {
                "name": "Test Store",
                "shop_url": "https://example.myshopify.com",
                "access_token": "dummy",
                "webhook_secret": "secret",
                "active": True,
            }
        )

        raw = b'{"id": 123}'
        valid, reason = handler.validate_webhook_request(store, raw, None, "wh_1")
        self.assertFalse(valid)
        self.assertIn("Invalid webhook signature", reason)

        valid, reason = handler.validate_webhook_request(store, raw, self._hmac("secret", raw), None)
        self.assertFalse(valid)
        self.assertIn("Missing webhook delivery id", reason)

        valid, reason = handler.validate_webhook_request(store, raw, self._hmac("secret", raw), "wh_1")
        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_register_webhook_delivery_dedupes(self):
        Store = self.env["shopify.store"].sudo()
        handler = self.env["shopify.webhook.handler"].sudo()
        Event = self.env["shopify.webhook.event"].sudo()

        store = Store.create(
            {
                "name": "Test Store",
                "shop_url": "https://example.myshopify.com",
                "access_token": "dummy",
                "webhook_secret": "secret",
                "active": True,
            }
        )

        event1 = handler.register_webhook_delivery(
            store=store,
            webhook_id="wh_1",
            topic="orders/create",
            shop_domain="example.myshopify.com",
            raw_payload="{}",
        )
        self.assertTrue(event1)
        self.assertEqual(Event.search_count([("store_id", "=", store.id), ("webhook_id", "=", "wh_1")]), 1)

        event2 = handler.register_webhook_delivery(
            store=store,
            webhook_id="wh_1",
            topic="orders/create",
            shop_domain="example.myshopify.com",
            raw_payload="{}",
        )
        self.assertFalse(event2)
        self.assertEqual(Event.search_count([("store_id", "=", store.id), ("webhook_id", "=", "wh_1")]), 1)

    def test_process_webhook_order_idempotent_pending(self):
        Store = self.env["shopify.store"].sudo()
        handler = self.env["shopify.webhook.handler"].sudo()
        Queue = self.env["shopify.order.queue"].sudo()

        store = Store.create(
            {
                "name": "Test Store",
                "shop_url": "https://example.myshopify.com",
                "access_token": "dummy",
                "webhook_secret": "secret",
                "active": True,
                "manage_orders_webhook": True,
            }
        )

        payload = {"id": 999}
        handler.process_webhook_order(payload, store)
        self.assertEqual(Queue.search_count([("store_id", "=", store.id), ("shopify_order_id", "=", "999")]), 1)

        # Calling again should not create a second pending/processing queue item
        handler.process_webhook_order(payload, store)
        self.assertEqual(Queue.search_count([("store_id", "=", store.id), ("shopify_order_id", "=", "999")]), 1)

