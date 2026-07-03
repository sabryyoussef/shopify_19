from odoo.tests.common import TransactionCase

from ..services.order_update_service import OrderUpdateService


class TestOrderUpdateSync(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Update Test Store",
                "shop_url": "https://update-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "order_edit_sync_mode": "draft_sent",
            }
        )
        self.partner = self.env["res.partner"].create({"name": "Update Customer"})
        self.product = self.env["product.product"].create(
            {"name": "Update Widget", "type": "consu", "list_price": 50.0, "default_code": "UPD-1"}
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": "update-order-1",
                "shopify_instance_id": self.store.id,
                "state": "draft",
            }
        )
        self.line = self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.product.id,
                "product_uom_qty": 2,
                "price_unit": 50.0,
                "shopify_line_item_id": "line-100",
            }
        )
        self.service = OrderUpdateService(self.env)

    def test_update_line_qty_on_draft_order(self):
        payload = {
            "id": "update-order-1",
            "line_items": [
                {
                    "id": "line-100",
                    "variant_id": 1,
                    "sku": "UPD-1",
                    "name": "Update Widget",
                    "price": "50.00",
                    "quantity": 5,
                }
            ],
            "total_price": "250.00",
        }
        self.service.update_order_from_payload(self.order, payload, self.store)
        self.assertEqual(self.line.product_uom_qty, 5.0)
