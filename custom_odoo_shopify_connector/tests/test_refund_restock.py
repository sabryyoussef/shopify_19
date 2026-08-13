from odoo.tests.common import TransactionCase

from ..services.refund_sync_service import RefundSyncService
from ..services.return_picking_service import ReturnPickingService


class TestRefundRestock(TransactionCase):
    def setUp(self):
        super().setUp()
        self.warehouse = self.env["stock.warehouse"].search([], limit=1)
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Restock Test Store",
                "shop_url": "https://restock-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "company_id": self.warehouse.company_id.id,
                "refund_restock_mode": "odoo_restock",
                "default_return_warehouse_id": self.warehouse.id,
            }
        )
        self.partner = self.env["res.partner"].create({"name": "Restock Customer"})
        self.product = self.env["product.product"].create(
            {"name": "Restock Widget", "type": "consu", "list_price": 50.0}
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": "restock-order-1",
                "shopify_instance_id": self.store.id,
            }
        )
        self.line = self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.product.id,
                "product_uom_qty": 2,
                "price_unit": 50.0,
                "shopify_line_item_id": "line-200",
            }
        )
        self.refund_service = RefundSyncService(self.env)
        self.return_service = ReturnPickingService(self.env)

    def test_restock_skipped_when_credit_note_only(self):
        self.store.refund_restock_mode = "credit_note_only"
        mapped = [
            {
                "sale_line": self.line,
                "qty": 1.0,
                "amount": 50.0,
                "line_item_id": "line-200",
            }
        ]
        payload = {
            "refund_line_items": [
                {"line_item_id": "line-200", "quantity": 1, "restock_type": "return"}
            ]
        }
        pickings = self.return_service.create_return_from_refund(
            self.store, self.order, payload, mapped
        )
        self.assertFalse(pickings)

    def test_restock_creates_picking_when_enabled(self):
        mapped = [
            {
                "sale_line": self.line,
                "qty": 1.0,
                "amount": 50.0,
                "line_item_id": "line-200",
            }
        ]
        payload = {
            "refund_line_items": [
                {"line_item_id": "line-200", "quantity": 1, "restock_type": "return"}
            ]
        }
        pickings = self.return_service.create_return_from_refund(
            self.store, self.order, payload, mapped
        )
        self.assertTrue(pickings)
        self.assertEqual(pickings[0].picking_type_code, "incoming")
