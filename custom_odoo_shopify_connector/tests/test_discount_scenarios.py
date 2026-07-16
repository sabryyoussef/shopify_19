from odoo.tests.common import TransactionCase
from odoo.tools import float_compare

from ..services.order_service import OrderService


class TestDiscountScenarios(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Discount Test Store",
                "shop_url": "https://discount-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": True,
            }
        )
        self.service = OrderService(self.env)

    def _base_payload(self, order_id, line_items, **extra):
        payload = {
            "id": order_id,
            "name": "#D%s" % order_id,
            "email": "discount@example.com",
            "customer": {
                "id": 5000 + int(str(order_id)[-3:]),
                "email": "discount@example.com",
                "first_name": "Disc",
                "last_name": "Test",
            },
            "line_items": line_items,
            "total_discounts": "0.00",
            "financial_status": "paid",
            "currency": "USD",
        }
        payload.update(extra)
        return payload

    def _import(self, payload):
        return self.service.create_order_from_payload(payload, self.store)

    def test_01_order_without_discount(self):
        payload = self._base_payload(
            91001,
            [
                {
                    "id": 1,
                    "variant_id": 81001,
                    "sku": "DISC-NONE",
                    "name": "No Discount",
                    "price": "100.00",
                    "quantity": 1,
                    "total_discount": "0.00",
                }
            ],
        )
        order = self._import(payload)
        line = order.order_line.filtered(lambda l: not l.display_type)[:1]
        self.assertEqual(line.discount, 0.0)
        self.assertFalse(order.shopify_discount_source)
        self.assertEqual(float_compare(order.amount_untaxed, 100.0, precision_digits=2), 0)

    def test_02_coupon_discount(self):
        payload = self._base_payload(
            91002,
            [
                {
                    "id": 2,
                    "variant_id": 81002,
                    "sku": "DISC-COUPON",
                    "name": "Coupon Item",
                    "price": "100.00",
                    "quantity": 1,
                    "discount_allocations": [{"amount": "10.00", "discount_application_index": 0}],
                }
            ],
            discount_applications=[{"type": "discount_code", "code": "SAVE10", "target_type": "line_item"}],
            total_discounts="10.00",
        )
        order = self._import(payload)
        line = order.order_line.filtered(lambda l: l.product_id.default_code == "DISC-COUPON")
        self.assertAlmostEqual(line.discount, 10.0, places=2)
        self.assertIn("coupon", order.shopify_discount_source or "")
        self.assertEqual(float_compare(order.amount_untaxed, 90.0, precision_digits=2), 0)

    def test_03_automatic_discount(self):
        payload = self._base_payload(
            91003,
            [
                {
                    "id": 3,
                    "variant_id": 81003,
                    "sku": "DISC-AUTO",
                    "name": "Auto Item",
                    "price": "80.00",
                    "quantity": 1,
                    "discount_allocations": [{"amount": "8.00", "discount_application_index": 0}],
                }
            ],
            discount_applications=[{"type": "automatic", "title": "Auto 10%", "target_type": "line_item"}],
            total_discounts="8.00",
        )
        order = self._import(payload)
        self.assertIn("automatic", order.shopify_discount_source or "")
        line = order.order_line.filtered(lambda l: l.product_id.default_code == "DISC-AUTO")
        self.assertAlmostEqual(line.discount, 10.0, places=2)

    def test_04_discount_on_single_line_only(self):
        payload = self._base_payload(
            91004,
            [
                {
                    "id": 41,
                    "variant_id": 81041,
                    "sku": "DISC-ONE",
                    "name": "Discounted",
                    "price": "50.00",
                    "quantity": 1,
                    "discount_allocations": [{"amount": "5.00"}],
                },
                {
                    "id": 42,
                    "variant_id": 81042,
                    "sku": "DISC-FULL",
                    "name": "Full Price",
                    "price": "50.00",
                    "quantity": 1,
                    "total_discount": "0.00",
                },
            ],
            total_discounts="5.00",
        )
        order = self._import(payload)
        disc_line = order.order_line.filtered(lambda l: l.product_id.default_code == "DISC-ONE")
        full_line = order.order_line.filtered(lambda l: l.product_id.default_code == "DISC-FULL")
        self.assertAlmostEqual(disc_line.discount, 10.0, places=2)
        self.assertEqual(full_line.discount, 0.0)
        self.assertEqual(float_compare(order.amount_untaxed, 95.0, precision_digits=2), 0)

    def test_05_discount_spread_across_lines(self):
        payload = self._base_payload(
            91005,
            [
                {
                    "id": 51,
                    "variant_id": 81051,
                    "sku": "DISC-A",
                    "name": "A",
                    "price": "100.00",
                    "quantity": 1,
                    "discount_allocations": [{"amount": "10.00"}],
                },
                {
                    "id": 52,
                    "variant_id": 81052,
                    "sku": "DISC-B",
                    "name": "B",
                    "price": "100.00",
                    "quantity": 1,
                    "discount_allocations": [{"amount": "10.00"}],
                },
            ],
            total_discounts="20.00",
            discount_applications=[{"type": "discount_code", "code": "MULTI"}],
        )
        order = self._import(payload)
        lines = order.order_line.filtered(lambda l: l.product_id.default_code in ("DISC-A", "DISC-B"))
        for line in lines:
            self.assertAlmostEqual(line.discount, 10.0, places=2)
        self.assertEqual(float_compare(order.amount_untaxed, 180.0, precision_digits=2), 0)

    def test_06_total_discounts_without_allocations(self):
        payload = self._base_payload(
            91006,
            [
                {
                    "id": 61,
                    "variant_id": 81061,
                    "sku": "DISC-ORD",
                    "name": "Order Level",
                    "price": "100.00",
                    "quantity": 1,
                }
            ],
            total_discounts="15.00",
        )
        order = self._import(payload)
        line = order.order_line.filtered(lambda l: l.product_id.default_code == "DISC-ORD")
        self.assertAlmostEqual(line.discount, 15.0, places=2)
        self.assertEqual(float_compare(order.amount_untaxed, 85.0, precision_digits=2), 0)
        self.assertTrue(order.shopify_discount_source)

    def test_07_allocations_without_total_discounts(self):
        payload = self._base_payload(
            91007,
            [
                {
                    "id": 71,
                    "variant_id": 81071,
                    "sku": "DISC-ALLOC",
                    "name": "Alloc Only",
                    "price": "200.00",
                    "quantity": 1,
                    "discount_allocations": [{"amount": "40.00"}],
                }
            ],
            total_discounts="0.00",
            discount_applications=[{"type": "discount_code", "code": "ALLOC40"}],
        )
        order = self._import(payload)
        line = order.order_line.filtered(lambda l: l.product_id.default_code == "DISC-ALLOC")
        self.assertAlmostEqual(line.discount, 20.0, places=2)
        self.assertEqual(float_compare(order.amount_untaxed, 160.0, precision_digits=2), 0)

    def test_08_duplicate_webhook_does_not_double_discount(self):
        payload = self._base_payload(
            91008,
            [
                {
                    "id": 81,
                    "variant_id": 81081,
                    "sku": "DISC-DUP",
                    "name": "Dup",
                    "price": "100.00",
                    "quantity": 1,
                    "discount_allocations": [{"amount": "25.00"}],
                }
            ],
            total_discounts="25.00",
            discount_applications=[{"type": "discount_code", "code": "DUP"}],
        )
        first = self._import(payload)
        second = self._import(payload)
        self.assertEqual(first.id, second.id)
        lines = first.order_line.filtered(lambda l: l.product_id.default_code == "DISC-DUP")
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines.discount, 25.0, places=2)
        self.assertEqual(float_compare(first.amount_untaxed, 75.0, precision_digits=2), 0)
        self.assertGreaterEqual(first.amount_untaxed, 0.0)
        self.assertGreaterEqual(first.amount_total, 0.0)
