import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestQueueClaiming(TransactionCase):
    def test_order_queue_claims_pending(self):
        Store = self.env["shopify.store"].sudo()
        Queue = self.env["shopify.order.queue"].sudo()

        store = Store.create(
            {
                "name": "Test Store",
                "shop_url": "https://example.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "webhook_secret": "secret",
            }
        )

        q1 = Queue.create({"store_id": store.id, "shopify_order_id": "1", "payload": json.dumps({"id": 1}), "state": "pending"})
        q2 = Queue.create({"store_id": store.id, "shopify_order_id": "2", "payload": json.dumps({"id": 2}), "state": "pending"})
        q3 = Queue.create({"store_id": store.id, "shopify_order_id": "3", "payload": json.dumps({"id": 3}), "state": "done"})

        claimed = Queue._claim_pending_queues(limit=1)
        self.assertEqual(len(claimed), 1)
        claimed.invalidate_cache(["state"])
        self.assertEqual(claimed[0].state, "processing")

        # Only one should be processing; one still pending; done stays done
        self.assertEqual(Queue.search_count([("id", "=", q1.id), ("state", "=", "processing")]) + Queue.search_count([("id", "=", q2.id), ("state", "=", "processing")]), 1)
        self.assertEqual(Queue.search_count([("state", "=", "pending")]), 1)
        self.assertEqual(Queue.browse(q3.id).state, "done")

    def test_import_queue_claim_for_processing_idempotent(self):
        Store = self.env["shopify.store"].sudo()
        Queue = self.env["shopify.import.queue"].sudo()

        store = Store.create(
            {
                "name": "Test Store",
                "shop_url": "https://example.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "webhook_secret": "secret",
            }
        )

        q = Queue.create(
            {
                "store_id": store.id,
                "operation_type": "import_shipped_orders",
                "status": "pending",
                "start_date": "2026-01-01 00:00:00",
                "end_date": "2026-01-02 00:00:00",
            }
        )
        self.assertTrue(q._claim_for_processing())
        self.assertFalse(q._claim_for_processing())

    def test_process_queue_keeps_translation_function_callable(self):
        Store = self.env["shopify.store"].sudo()
        Queue = self.env["shopify.order.queue"].sudo()

        store = Store.create(
            {
                "name": "Test Store",
                "shop_url": "https://example.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "webhook_secret": "secret",
            }
        )

        failing_queue = Queue.create(
            {
                "store_id": store.id,
                "shopify_order_id": "fail",
                "payload": json.dumps({"id": "fail"}),
                "state": "pending",
            }
        )
        succeeding_queue = Queue.create(
            {
                "store_id": store.id,
                "shopify_order_id": "ok",
                "payload": json.dumps({"id": "ok"}),
                "state": "pending",
            }
        )

        def fake_import(_store, order_data, **_kwargs):
            if order_data.get("id") == "fail":
                raise ValueError("boom")
            return True, None

        with patch(
            "odoo.addons.custom_odoo_shopify_connector.models.order_queue.OrderImportService.import_shopify_order",
            side_effect=fake_import,
        ):
            Queue.process_queue(limit=50)

        failing_queue.invalidate_cache(["state", "log_message"])
        succeeding_queue.invalidate_cache(["state", "log_message"])
        self.assertEqual(failing_queue.state, "failed")
        self.assertEqual(succeeding_queue.state, "done")
        self.assertEqual(succeeding_queue.log_message, "Processed successfully")

