from odoo.tests.common import TransactionCase

from ..services.shopify_api import ShopifyAPI


class TestShopifyApiMethods(TransactionCase):
    def test_get_customers_returns_customers_list(self):
        api = ShopifyAPI("example.myshopify.com", "token")

        def fake_request(method, path, params=None, data=None, max_retries=5):
            self.assertEqual(method, "GET")
            self.assertEqual(path, "/customers.json")
            self.assertEqual((params or {}).get("limit"), 250)
            return {"customers": [{"id": 1, "email": "a@example.com"}]}

        api._request = fake_request
        customers = api.get_customers()

        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0]["id"], 1)

    def test_upsert_product_metafield_updates_when_exists(self):
        api = ShopifyAPI("example.myshopify.com", "token")

        api.get_product_metafields = lambda product_id, **params: [
            {"id": 10, "namespace": "odoo_custom", "key": "brand"}
        ]

        calls = []

        def fake_request(method, path, params=None, data=None, max_retries=5):
            calls.append((method, path, data))
            return {"metafield": {"id": 10}}

        api._request = fake_request
        api.upsert_product_metafield(
            product_id=123,
            namespace="odoo_custom",
            key="brand",
            mtype="single_line_text_field",
            value="Acme",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "PUT")
        self.assertEqual(calls[0][1], "/metafields/10.json")

    def test_upsert_product_metafield_creates_when_missing(self):
        api = ShopifyAPI("example.myshopify.com", "token")

        api.get_product_metafields = lambda product_id, **params: []

        calls = []

        def fake_request(method, path, params=None, data=None, max_retries=5):
            calls.append((method, path, data))
            return {"metafield": {"id": 11}}

        api._request = fake_request
        api.upsert_product_metafield(
            product_id=123,
            namespace="odoo_custom",
            key="brand",
            mtype="single_line_text_field",
            value="Acme",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "/products/123/metafields.json")
