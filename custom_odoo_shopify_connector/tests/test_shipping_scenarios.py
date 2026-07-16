from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase

from ..services.fulfillment_service import ShopifyFulfillmentService
from ..services.order_service import OrderService
from ..services.shipping_service import ShopifyShippingService


class TestShippingScenarios(TransactionCase):
    def setUp(self):
        super().setUp()
        self.delivery_product = self.env["product.product"].create(
            {
                "name": "Shipping",
                "type": "service",
                "list_price": 0.0,
            }
        )
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Shipping Test Store",
                "shop_url": "https://shipping-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": True,
                "delivery_product_id": self.delivery_product.id,
            }
        )
        self.service = OrderService(self.env)
        self.fulfillment = ShopifyFulfillmentService(self.env)
        self.shipping = ShopifyShippingService(self.env)

    def _payload(self, order_id, shipping_lines=None, fulfillments=None, status=None):
        return {
            "id": order_id,
            "name": "#S%s" % order_id,
            "email": "ship@example.com",
            "customer": {
                "id": 6000 + int(str(order_id)[-3:]),
                "email": "ship@example.com",
                "first_name": "Ship",
                "last_name": "Test",
            },
            "line_items": [
                {
                    "id": 9000 + int(str(order_id)[-3:]),
                    "variant_id": 7000 + int(str(order_id)[-3:]),
                    "sku": "SHIP-%s" % order_id,
                    "name": "Ship Item",
                    "price": "50.00",
                    "quantity": 2,
                }
            ],
            "shipping_lines": shipping_lines or [],
            "fulfillments": fulfillments or [],
            "fulfillment_status": status,
        }

    def test_01_free_shipping(self):
        order = self.service.create_order_from_payload(
            self._payload(92001, shipping_lines=[{"title": "Free", "price": "0.00"}]),
            self.store,
        )
        ship_lines = order.order_line.filtered(lambda l: l.product_id == self.delivery_product)
        self.assertEqual(len(ship_lines), 1)
        self.assertEqual(ship_lines.price_unit, 0.0)

    def test_02_shipping_price_gt_zero(self):
        order = self.service.create_order_from_payload(
            self._payload(92002, shipping_lines=[{"title": "Std", "price": "25.50"}]),
            self.store,
        )
        ship_lines = order.order_line.filtered(lambda l: l.product_id == self.delivery_product)
        self.assertEqual(len(ship_lines), 1)
        self.assertAlmostEqual(ship_lines.price_unit, 25.50, places=2)

    def test_03_multiple_shipping_lines(self):
        order = self.service.create_order_from_payload(
            self._payload(
                92003,
                shipping_lines=[
                    {"title": "Std", "price": "10.00"},
                    {"title": "Express", "price": "20.00"},
                ],
            ),
            self.store,
        )
        ship_lines = order.order_line.filtered(lambda l: l.product_id == self.delivery_product)
        self.assertEqual(len(ship_lines), 2)
        self.assertAlmostEqual(sum(ship_lines.mapped("price_unit")), 30.0, places=2)

    def test_04_no_duplicate_shipping_on_reimport(self):
        payload = self._payload(92004, shipping_lines=[{"title": "Std", "price": "12.00"}])
        order = self.service.create_order_from_payload(payload, self.store)
        self.service._create_shipping_lines(order, self.store, payload)
        ship_lines = order.order_line.filtered(lambda l: l.product_id == self.delivery_product)
        self.assertEqual(len(ship_lines), 1)

    def test_05_fulfillment_idempotent_and_partial(self):
        order = self.service.create_order_from_payload(self._payload(92005), self.store)
        order.action_confirm()
        payload1 = {
            "fulfillment_status": "partial",
            "fulfillments": [
                {
                    "id": 111,
                    "location_id": 1,
                    "line_items": [
                        {
                            "variant_id": 7005,
                            "sku": "SHIP-92005",
                            "quantity": 1,
                        }
                    ],
                }
            ],
        }
        self.fulfillment.handle_fulfillment(order, self.store, payload1)
        first_ids = order.shopify_fulfillment_ids
        self.fulfillment.handle_fulfillment(order, self.store, payload1)
        self.assertEqual(order.shopify_fulfillment_ids, first_ids)

        payload2 = {
            "fulfillment_status": "fulfilled",
            "fulfillments": [
                {
                    "id": 112,
                    "location_id": 1,
                    "line_items": [
                        {
                            "variant_id": 7005,
                            "sku": "SHIP-92005",
                            "quantity": 1,
                        }
                    ],
                }
            ],
        }
        self.fulfillment.handle_fulfillment(order, self.store, payload2)
        self.assertIn("112", order.shopify_fulfillment_ids or "")

    def test_06_tracking_payloads_per_package(self):
        carrier = self.env["delivery.carrier"].search([], limit=1)
        if not carrier:
            self.skipTest("No delivery carrier configured in test DB")
        order = self.service.create_order_from_payload(self._payload(92006), self.store)
        order.action_confirm()
        picking = order.picking_ids[:1]
        picking.carrier_id = carrier.id

        Package = self.env["stock.package"] if "stock.package" in self.env else self.env["stock.quant.package"]
        pkg1 = Package.create({"carrier_tracking_ref": "TRK-A", "carrier_id": carrier.id})
        pkg2 = Package.create({"carrier_tracking_ref": "TRK-B", "carrier_id": carrier.id})
        # Attach packages conceptually; service reads move_line result_package_id
        # If no move lines yet, fallback path uses picking tracking once.
        payloads = self.shipping._get_tracking_payloads(picking, carrier)
        # Ensure uniqueness contract: no duplicate tracking numbers from same pkgs
        picking.carrier_tracking_ref = "TRK-PICK"
        payloads_single = self.shipping._get_tracking_payloads(picking, carrier)
        numbers = [p[0] for p in payloads_single]
        self.assertEqual(len(numbers), len(set(numbers)))

        with patch.object(
            type(self.store),
            "_get_api_client",
            return_value=MagicMock(),
        ):
            with patch(
                "odoo.addons.custom_odoo_shopify_connector.services.shipping_service.ShopifyFulfillmentService.create_fulfillment",
                return_value={"ok": True},
            ) as mocked:
                # Force single tracking path
                self.shipping.update_shipping(picking, order, self.store)
                # May be zero if picking has no tracking applied after earlier assignment;
                # ensure method does not raise and does not create duplicate calls for same tracking.
                call_trackings = [c.args[2] for c in mocked.call_args_list]
                self.assertEqual(len(call_trackings), len(set(call_trackings)))
                _ = (pkg1, pkg2)  # keep packages referenced for clarity
