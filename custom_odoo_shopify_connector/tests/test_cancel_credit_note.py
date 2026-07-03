from odoo.tests.common import TransactionCase

from ..services.refund_sync_service import RefundSyncService


class TestCancelCreditNote(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Cancel Test Store",
                "shop_url": "https://cancel-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "cancel_sync_mode": "credit_note",
            }
        )
        self.partner = self.env["res.partner"].create({"name": "Cancel Customer"})
        self.product = self.env["product.product"].create(
            {"name": "Cancel Widget", "type": "consu", "list_price": 100.0}
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": "cancel-order-1",
                "shopify_instance_id": self.store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 100.0,
            }
        )
        self.order.action_confirm()
        invoice = self.order._create_invoices()
        invoice.action_post()
        self.refund_service = RefundSyncService(self.env)

    def test_cancel_creates_credit_note(self):
        credit = self.refund_service.sync_cancel_reversal(self.store, self.order, "customer")
        self.assertTrue(credit)
        self.assertEqual(credit.move_type, "out_refund")
        self.assertEqual(credit.reversed_entry_id.move_type, "out_invoice")

    def test_cancel_credit_note_idempotent(self):
        first = self.refund_service.sync_cancel_reversal(self.store, self.order, "customer")
        second = self.refund_service.sync_cancel_reversal(self.store, self.order, "customer")
        self.assertTrue(first)
        self.assertFalse(second)
