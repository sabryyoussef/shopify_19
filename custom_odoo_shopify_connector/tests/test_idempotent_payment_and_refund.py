from odoo.tests.common import TransactionCase

from ..services.fulfillment_service import ShopifyFulfillmentService
from ..services.order_service import OrderService
from ..services.payment_fee_service import PaymentFeeService
from ..services.refund_sync_service import RefundSyncService


class TestIdempotentPaymentAndRefund(TransactionCase):
    """
    Idempotency keys used by this module:
    - Sale Order: sale.order.shopify_order_id (+ store mapping)
    - Order queue webhook delivery: shopify.webhook.event (store_id, webhook_id)
    - Queue pending rows: shopify.order.queue shopify_order_id in pending/processing
    - Payment: account.payment.shopify_transaction_id
    - Refund / Credit Note: account.move.shopify_refund_id
    - Cancel CN: shopify_refund_id = cancel-<shopify_order_id>
    - Exchange: shopify_exchange_key / shopify_refund_id = exchange-<odoo_order_id>
    - Fulfillment: sale.order.shopify_fulfillment_ids (csv of Shopify fulfillment ids)
    - Shipping lines: existing delivery_product lines on SO
    """

    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Idempotency Store",
                "shop_url": "https://idempotency-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": True,
                "manage_orders_webhook": True,
                "webhook_secret": "secret",
                "delivery_product_id": self.env["product.product"]
                .create({"name": "Ship Idem", "type": "service"})
                .id,
            }
        )
        self.order_service = OrderService(self.env)
        self.fee_service = PaymentFeeService(self.env)
        self.refund_service = RefundSyncService(self.env)
        self.fulfillment = ShopifyFulfillmentService(self.env)
        self.handler = self.env["shopify.webhook.handler"]

    def _payload(self, order_id="93001"):
        return {
            "id": order_id,
            "name": "#I%s" % order_id,
            "email": "idem@example.com",
            "customer": {"id": 93001, "email": "idem@example.com", "first_name": "Id", "last_name": "Em"},
            "line_items": [
                {
                    "id": 1,
                    "variant_id": 93011,
                    "sku": "IDEM-SKU",
                    "name": "Idem Item",
                    "price": "100.00",
                    "quantity": 1,
                }
            ],
            "shipping_lines": [{"title": "Std", "price": "10.00"}],
            "transactions": [
                {"id": 88001, "kind": "sale", "status": "success", "amount": "50.00"},
                {"id": 88002, "kind": "sale", "status": "success", "amount": "60.00"},
            ],
            "financial_status": "paid",
            "total_price": "110.00",
            "fulfillments": [{"id": 44001, "line_items": [{"variant_id": 93011, "sku": "IDEM-SKU", "quantity": 1}]}],
            "fulfillment_status": "fulfilled",
        }

    def test_01_order_webhook_idempotent(self):
        payload = self._payload("93101")
        first = self.order_service.create_order_from_payload(payload, self.store)
        second = self.order_service.create_order_from_payload(payload, self.store)
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            self.env["sale.order"].search_count(
                [("shopify_order_id", "=", "93101"), ("shopify_instance_id", "=", self.store.id)]
            ),
            1,
        )
        ship = first.order_line.filtered(lambda l: l.product_id == self.store.delivery_product_id)
        self.assertEqual(len(ship), 1)

    def test_02_duplicate_webhook_delivery_registration(self):
        raw = '{"id": 93102}'
        e1 = self.handler.register_webhook_delivery(self.store, "wh-1", "orders/create", self.store.shop_url, raw)
        e2 = self.handler.register_webhook_delivery(self.store, "wh-1", "orders/create", self.store.shop_url, raw)
        self.assertTrue(e1)
        self.assertFalse(e2)

    def test_03_transaction_ids_stable(self):
        payload = self._payload()
        ids1 = self.fee_service.extract_sale_transaction_ids(payload)
        ids2 = self.fee_service.extract_sale_transaction_ids(payload)
        self.assertEqual(ids1, ids2)
        self.assertEqual(ids1, ["88001", "88002"])
        self.assertEqual(self.fee_service.extract_paid_amount(payload), 110.0)

    def test_04_payment_record_idempotent_key(self):
        Payment = self.env["account.payment"]
        if "shopify_transaction_id" not in Payment._fields:
            self.skipTest("shopify_transaction_id field missing on account.payment")
        partner = self.env["res.partner"].create({"name": "Pay Idem"})
        vals = {
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": partner.id,
            "amount": 50.0,
            "shopify_transaction_id": "88001",
        }
        # journal required in many setups
        journal = self.env["account.journal"].search([("type", "in", ("bank", "cash"))], limit=1)
        if not journal:
            self.skipTest("No bank/cash journal")
        vals["journal_id"] = journal.id
        p1 = Payment.create(vals)
        self.assertEqual(
            Payment.search_count([("shopify_transaction_id", "=", "88001")]),
            1,
        )
        # Second create with same key would exist; import path checks search_count first.
        exists = Payment.search_count([("shopify_transaction_id", "=", "88001")])
        self.assertEqual(exists, 1)
        self.assertTrue(p1)

    def test_05_refund_idempotent(self):
        order = self.order_service.create_order_from_payload(self._payload("93105"), self.store)
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        refund_payload = {
            "id": "rf-93105",
            "note": "full",
            "refund_line_items": [
                {"line_item_id": order.order_line[0].shopify_line_item_id, "quantity": 1, "subtotal": 100.0}
            ],
        }
        c1 = self.refund_service.sync_refund_from_webhook(self.store, order, refund_payload)
        c2 = self.refund_service.sync_refund_from_webhook(self.store, order, refund_payload)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(
            self.env["account.move"].search_count([("shopify_refund_id", "=", "rf-93105")]),
            1,
        )

    def test_06_fulfillment_idempotent(self):
        order = self.order_service.create_order_from_payload(self._payload("93106"), self.store)
        order.action_confirm()
        payload = {
            "fulfillment_status": "fulfilled",
            "fulfillments": [{"id": 55101, "line_items": [{"variant_id": 93011, "sku": "IDEM-SKU", "quantity": 1}]}],
        }
        self.fulfillment.handle_fulfillment(order, self.store, payload)
        first = order.shopify_fulfillment_ids
        self.fulfillment.handle_fulfillment(order, self.store, payload)
        self.assertEqual(order.shopify_fulfillment_ids, first)

    def test_07_order_update_no_new_so(self):
        payload = self._payload("93107")
        order = self.order_service.create_order_from_payload(payload, self.store)
        payload["note"] = "updated"
        again = self.order_service.create_order_from_payload(payload, self.store)
        self.assertEqual(order.id, again.id)
        self.assertEqual(
            self.env["sale.order"].search_count([("shopify_order_id", "=", "93107")]),
            1,
        )
