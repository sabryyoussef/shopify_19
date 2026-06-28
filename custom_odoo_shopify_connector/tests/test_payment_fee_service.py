from odoo.tests.common import TransactionCase

from ..services.payment_fee_service import PaymentFeeService


class TestPaymentFeeService(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Store = self.env["shopify.store"].sudo()
        self.Gateway = self.env["shopify.payment.gateway"].sudo()
        self.Product = self.env["product.product"].sudo()
        self.store = self.Store.create(
            {
                "name": "Fee Test Store",
                "shop_url": "https://fee-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
            }
        )
        self.fee_product = self.Product.create({"name": "Payment Fees", "type": "service"})
        self.store.write({"payment_fee_product_id": self.fee_product.id})
        self.paymob = self.Gateway.create(
            {
                "name": "Paymob",
                "instance_id": self.store.id,
                "payment_code": "Paymob",
                "active": True,
                "fee_percent": 5.0,
                "fee_fixed": 2.0,
                "fee_apply_mode": "line_item",
                "fee_base": "subtotal",
                "fee_product_id": self.fee_product.id,
            }
        )
        self.cod = self.Gateway.create(
            {
                "name": "COD",
                "instance_id": self.store.id,
                "payment_code": "cash_on_delivery",
                "active": True,
                "fee_apply_mode": "none",
            }
        )
        self.service = PaymentFeeService(self.env)

    def _create_order_with_subtotal(self, amount):
        partner = self.env["res.partner"].create({"name": "Fee Customer"})
        product = self.Product.create({"name": "Widget", "list_price": amount})
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "shopify_instance_id": self.store.id,
                "shopify_order_id": "fee-order-1",
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.id,
                "product_uom_qty": 1,
                "price_unit": amount,
            }
        )
        return order

    def test_paymob_fee_line_item(self):
        order = self._create_order_with_subtotal(100.0)
        payload = {
            "payment_gateway_names": ["Paymob"],
            "total_price": "100.00",
        }
        self.service.apply_fees_for_order(order, self.store, payload)
        fee_lines = order.order_line.filtered(lambda l: l.product_id == self.fee_product)
        self.assertEqual(len(fee_lines), 1)
        self.assertEqual(fee_lines.price_unit, 7.0)
        self.assertEqual(order.shopify_gateway_fee, 7.0)
        self.assertEqual(order.shopify_net_received, 93.0)

    def test_cod_zero_fee(self):
        order = self._create_order_with_subtotal(50.0)
        payload = {"payment_gateway_names": ["cash_on_delivery"], "total_price": "50.00"}
        self.service.apply_fees_for_order(order, self.store, payload)
        fee_lines = order.order_line.filtered(lambda l: l.product_id == self.fee_product)
        self.assertFalse(fee_lines)
        self.assertEqual(order.shopify_gateway_fee, 0.0)

    def test_invoice_discount_mode_stores_pending(self):
        self.paymob.write({"fee_apply_mode": "invoice_discount"})
        order = self._create_order_with_subtotal(200.0)
        payload = {"payment_gateway_names": ["Paymob"], "total_price": "200.00"}
        self.service.apply_fees_for_order(order, self.store, payload)
        self.assertEqual(order.shopify_fee_discount_pending, 12.0)
        fee_lines = order.order_line.filtered(lambda l: l.product_id == self.fee_product)
        self.assertFalse(fee_lines)
