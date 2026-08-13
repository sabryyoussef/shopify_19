from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..services.order_service import OrderService


class TestAutoCreateProduct(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Auto Create Test Store",
                "shop_url": "https://auto-create-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": False,
            }
        )
        self.service = OrderService(self.env)

    def test_missing_product_raises_when_flag_off(self):
        item = {
            "id": 1001,
            "variant_id": 9001,
            "sku": "MISSING-SKU",
            "name": "Missing Product",
            "price": "10.00",
            "quantity": 1,
        }
        lookups = self.service._prepare_line_product_lookups(self.store, [item])
        with self.assertRaises(ValidationError):
            self.service._resolve_line_product(item, self.store, *lookups)

    def test_missing_product_created_when_flag_on(self):
        self.store.auto_create_product_if_not_found = True
        item = {
            "id": 1002,
            "variant_id": 9002,
            "sku": "NEW-SKU",
            "name": "New Product",
            "price": "15.00",
            "quantity": 1,
        }
        lookups = self.service._prepare_line_product_lookups(self.store, [item])
        product = self.service._resolve_line_product(item, self.store, *lookups)
        self.assertTrue(product)
        self.assertEqual(product.default_code, "NEW-SKU")
