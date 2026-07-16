from odoo.tests.common import TransactionCase

from ..services.refund_sync_service import RefundSyncService


class TestCancelBeforeInvoice(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Cancel Before Invoice Store",
                "shop_url": "https://cancel-before.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "cancel_sync_mode": "credit_note",
            }
        )
        self.partner = self.env["res.partner"].create({"name": "Cancel Before Customer"})
        self.product = self.env["product.product"].create(
            {"name": "Cancel Before Widget", "type": "consu", "list_price": 100.0}
        )
        self.service = RefundSyncService(self.env)

    def _order(self, name, confirm=False, invoice_draft=False):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": name,
                "shopify_instance_id": self.store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 100.0,
            }
        )
        if confirm:
            order.action_confirm()
        if invoice_draft:
            order._create_invoices()
        return order

    def test_01_draft_quotation_cancel(self):
        order = self._order("cbi-draft")
        self.assertEqual(order.state, "draft")
        self.service.cancel_order_from_shopify(self.store, order, "customer")
        self.assertEqual(order.state, "cancel")
        self.assertTrue(order.shopify_cancelled)
        self.assertFalse(
            self.env["account.move"].search_count(
                [("shopify_refund_id", "=", "cancel-%s" % order.shopify_order_id)]
            )
        )

    def test_02_confirmed_without_invoice(self):
        order = self._order("cbi-confirmed", confirm=True)
        self.assertTrue(order.picking_ids)
        self.service.cancel_order_from_shopify(self.store, order, "customer")
        self.assertEqual(order.state, "cancel")
        self.assertTrue(all(p.state == "cancel" for p in order.picking_ids))
        self.assertFalse(
            self.env["account.move"].search_count(
                [("shopify_refund_id", "=", "cancel-%s" % order.shopify_order_id)]
            )
        )

    def test_03_confirmed_with_draft_invoice(self):
        order = self._order("cbi-draft-inv", confirm=True, invoice_draft=True)
        draft = order.invoice_ids.filtered(lambda m: m.state == "draft")
        self.assertTrue(draft)
        self.service.cancel_order_from_shopify(self.store, order, "customer")
        self.assertEqual(order.state, "cancel")
        draft.invalidate_recordset()
        self.assertTrue(all(inv.state == "cancel" for inv in order.invoice_ids))
        self.assertFalse(
            self.env["account.move"].search_count(
                [
                    ("move_type", "=", "out_refund"),
                    ("shopify_refund_id", "=", "cancel-%s" % order.shopify_order_id),
                ]
            )
        )

    def test_04_open_picking_cancelled(self):
        order = self._order("cbi-picking", confirm=True)
        open_pickings = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
        self.assertTrue(open_pickings)
        self.service.cancel_order_from_shopify(self.store, order, "inventory")
        self.assertTrue(all(p.state == "cancel" for p in open_pickings))

    def test_05_duplicate_cancel_webhook(self):
        order = self._order("cbi-dup", confirm=True)
        self.service.cancel_order_from_shopify(self.store, order, "customer")
        self.service.cancel_order_from_shopify(self.store, order, "customer")
        self.assertEqual(order.state, "cancel")
        self.assertEqual(
            self.env["account.move"].search_count(
                [("shopify_refund_id", "=", "cancel-%s" % order.shopify_order_id)]
            ),
            0,
        )
