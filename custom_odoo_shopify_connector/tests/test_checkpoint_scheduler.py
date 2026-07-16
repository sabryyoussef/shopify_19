from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase


class _FakeShopifyClient:
    def __init__(self, orders=None, exc=None):
        self._orders = orders or []
        self._exc = exc

    def get_orders(self, **params):
        if self._exc:
            raise self._exc
        return list(self._orders)


class TestCheckpointScheduler(TransactionCase):
    def _deactivate_other_stores(self):
        """Prevent scheduler from calling real Shopify stores present in the DB."""
        self.env["shopify.store"].sudo().search([]).write({"active": False})

    def test_import_orders_scheduler_advances_checkpoint_after_enqueue(self):
        self._deactivate_other_stores()
        Store = self.env["shopify.store"].sudo()
        OrderQueue = self.env["shopify.order.queue"].sudo()

        store = Store.create(
            {
                "name": "Test Store Checkpoint A",
                "shop_url": "https://checkpoint-a.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "webhook_secret": "secret",
            }
        )

        orders = [
            {"id": 1, "created_at": "2026-04-20T10:00:00Z"},
            {"id": 2, "created_at": "2026-04-20T12:00:00Z"},
        ]

        with patch.object(
            type(store),
            "_get_api_client",
            return_value=_FakeShopifyClient(orders=orders),
        ):
            Store.import_orders_scheduler()

        self.assertEqual(OrderQueue.search_count([("store_id", "=", store.id)]), 2)
        self.assertTrue(store.last_order_import_time)

    def test_import_orders_scheduler_does_not_advance_on_fetch_failure(self):
        self._deactivate_other_stores()
        Store = self.env["shopify.store"].sudo()

        store = Store.create(
            {
                "name": "Test Store Checkpoint B",
                "shop_url": "https://checkpoint-b.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "webhook_secret": "secret",
                "last_order_import_time": fields.Datetime.from_string("2026-04-20 00:00:00"),
            }
        )
        before = store.last_order_import_time

        with patch.object(
            type(store),
            "_get_api_client",
            return_value=_FakeShopifyClient(exc=Exception("boom")),
        ):
            Store.import_orders_scheduler()

        self.assertEqual(store.last_order_import_time, before)

    def test_import_orders_scheduler_skips_store_without_access_token(self):
        self._deactivate_other_stores()
        Store = self.env["shopify.store"].sudo()
        OrderQueue = self.env["shopify.order.queue"].sudo()

        store = Store.create(
            {
                "name": "Unconfigured Store Checkpoint",
                "shop_url": "https://checkpoint-c.myshopify.com",
                "access_token": False,
                "active": True,
                "webhook_secret": "secret",
            }
        )

        Store.import_orders_scheduler()

        self.assertEqual(OrderQueue.search_count([("store_id", "=", store.id)]), 0)
