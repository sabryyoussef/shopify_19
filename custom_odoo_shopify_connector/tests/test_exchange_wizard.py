from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import float_compare
from unittest.mock import patch

from ..services.refund_sync_service import RefundSyncService


class TestExchangeWizard(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "Exchange Test Store",
                "shop_url": "https://exchange-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "refund_restock_mode": "odoo_restock",
            }
        )
        self.partner = self.env["res.partner"].create({"name": "Exchange Customer"})
        self.product_a = self.env["product.product"].create(
            {"name": "Orig Product", "type": "consu", "list_price": 100.0, "default_code": "EX-A"}
        )
        self.product_expensive = self.env["product.product"].create(
            {"name": "Expensive", "type": "consu", "list_price": 150.0, "default_code": "EX-EXP"}
        )
        self.product_cheap = self.env["product.product"].create(
            {"name": "Cheap", "type": "consu", "list_price": 60.0, "default_code": "EX-CHP"}
        )
        self.product_b = self.env["product.product"].create(
            {"name": "Second", "type": "consu", "list_price": 80.0, "default_code": "EX-B"}
        )
        self.refund_service = RefundSyncService(self.env)

    def _make_order(self, shopify_order_id, lines):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": shopify_order_id,
                "shopify_instance_id": self.store.id,
            }
        )
        for product, qty, price, line_id in lines:
            self.env["sale.order.line"].create(
                {
                    "order_id": order.id,
                    "product_id": product.id,
                    "product_uom_qty": qty,
                    "price_unit": price,
                    "shopify_line_item_id": line_id,
                }
            )
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        return order

    def _wizard(self, order, wizard_lines):
        wiz = self.env["shopify.exchange.wizard"].create(
            {
                "sale_order_id": order.id,
                "store_id": self.store.id,
                "note": "Test exchange",
                "line_ids": [(0, 0, vals) for vals in wizard_lines],
            }
        )
        return wiz

    def test_01_new_product_more_expensive(self):
        order = self._make_order(
            "ex-expensive",
            [(self.product_a, 1, 100.0, "L1")],
        )
        wiz = self._wizard(
            order,
            [
                {
                    "sale_line_id": order.order_line[0].id,
                    "return_qty": 1,
                    "replacement_product_id": self.product_expensive.id,
                    "replacement_qty": 1,
                    "replacement_price_unit": 150.0,
                }
            ],
        )
        action = wiz.action_process_exchange()
        replacement = self.env["sale.order"].browse(action["res_id"])
        self.assertTrue(replacement)
        self.assertEqual(float_compare(order.shopify_exchange_price_diff, 50.0, precision_digits=2), 0)
        self.assertTrue(
            replacement.order_line.filtered(lambda l: "balance due" in (l.name or "").lower() or l.price_unit == 50.0)
        )
        credit = self.env["account.move"].search([("shopify_refund_id", "=", "exchange-%s" % order.id)], limit=1)
        self.assertTrue(credit)
        self.assertEqual(credit.state, "posted")
        # Replacement must not be treated as auto-paid
        self.assertFalse(replacement.invoice_ids.filtered(lambda m: m.payment_state in ("paid", "in_payment")))

    def test_02_new_product_cheaper(self):
        order = self._make_order(
            "ex-cheap",
            [(self.product_a, 1, 100.0, "L2")],
        )
        wiz = self._wizard(
            order,
            [
                {
                    "sale_line_id": order.order_line[0].id,
                    "return_qty": 1,
                    "replacement_product_id": self.product_cheap.id,
                    "replacement_qty": 1,
                    "replacement_price_unit": 60.0,
                }
            ],
        )
        action = wiz.action_process_exchange()
        replacement = self.env["sale.order"].browse(action["res_id"])
        self.assertEqual(float_compare(order.shopify_exchange_price_diff, -40.0, precision_digits=2), 0)
        # Additional credit amount captured on CN mapping (no unexplained negative SO lines)
        self.assertFalse(replacement.order_line.filtered(lambda l: l.price_unit < 0))
        credit = self.env["account.move"].search([("shopify_refund_id", "=", "exchange-%s" % order.id)], limit=1)
        self.assertTrue(credit)
        # Documented as customer credit via credit note amount
        self.assertGreater(credit.amount_total, 0)

    def test_03_same_price(self):
        order = self._make_order(
            "ex-same",
            [(self.product_a, 1, 100.0, "L3")],
        )
        wiz = self._wizard(
            order,
            [
                {
                    "sale_line_id": order.order_line[0].id,
                    "return_qty": 1,
                    "replacement_product_id": self.product_a.id,
                    "replacement_qty": 1,
                    "replacement_price_unit": 100.0,
                }
            ],
        )
        wiz.action_process_exchange()
        self.assertEqual(float_compare(order.shopify_exchange_price_diff, 0.0, precision_digits=2), 0)
        children = order.shopify_exchange_child_ids
        self.assertEqual(len(children), 1)
        self.assertFalse(children.order_line.filtered(lambda l: "balance due" in (l.name or "").lower()))

    def test_04_partial_qty_exchange(self):
        order = self._make_order(
            "ex-partial",
            [(self.product_a, 3, 100.0, "L4")],
        )
        wiz = self._wizard(
            order,
            [
                {
                    "sale_line_id": order.order_line[0].id,
                    "return_qty": 1,
                    "replacement_product_id": self.product_cheap.id,
                    "replacement_qty": 1,
                    "replacement_price_unit": 60.0,
                }
            ],
        )
        wiz.action_process_exchange()
        self.assertEqual(float_compare(order.shopify_exchange_price_diff, -40.0, precision_digits=2), 0)

    def test_05_multi_product_exchange(self):
        order = self._make_order(
            "ex-multi",
            [
                (self.product_a, 1, 100.0, "L5A"),
                (self.product_b, 1, 80.0, "L5B"),
            ],
        )
        lines = order.order_line.filtered(lambda l: not l.display_type)
        wiz = self._wizard(
            order,
            [
                {
                    "sale_line_id": lines[0].id,
                    "return_qty": 1,
                    "replacement_product_id": self.product_expensive.id,
                    "replacement_qty": 1,
                    "replacement_price_unit": 150.0,
                },
                {
                    "sale_line_id": lines[1].id,
                    "return_qty": 1,
                    "replacement_product_id": self.product_cheap.id,
                    "replacement_qty": 1,
                    "replacement_price_unit": 60.0,
                },
            ],
        )
        wiz.action_process_exchange()
        # (150+60) - (100+80) = 30
        self.assertEqual(float_compare(order.shopify_exchange_price_diff, 30.0, precision_digits=2), 0)

    def test_06_duplicate_exchange_blocked(self):
        order = self._make_order(
            "ex-dup",
            [(self.product_a, 1, 100.0, "L6")],
        )
        vals = [
            {
                "sale_line_id": order.order_line[0].id,
                "return_qty": 1,
                "replacement_product_id": self.product_a.id,
                "replacement_qty": 1,
                "replacement_price_unit": 100.0,
            }
        ]
        self._wizard(order, vals).action_process_exchange()
        with self.assertRaises(UserError):
            self._wizard(order, vals).action_process_exchange()

    def test_07_fail_replacement_so_after_credit_note(self):
        """
        Simulate CN created then replacement SO failing: exchange key is set and
        a second wizard run is blocked by idempotency.
        """
        order = self._make_order(
            "ex-fail-so",
            [(self.product_a, 1, 100.0, "L7")],
        )
        invoice = self.refund_service._find_posted_invoice(order)
        sol = order.order_line[0]
        exchange_key = "exchange-%s" % order.id
        refund_payload = {
            "id": exchange_key,
            "refund_line_items": [
                {
                    "line_item_id": sol.shopify_line_item_id,
                    "quantity": 1,
                    "subtotal": 100.0,
                }
            ],
            "note": "Exchange failed mid-flight",
        }
        mapped = self.refund_service._map_refund_lines(order, refund_payload["refund_line_items"])
        credit = self.refund_service._create_partial_credit_note(
            self.store, order, invoice, refund_payload, mapped
        )
        self.assertTrue(credit)
        # Mid-flight failure marker (replacement SO did not complete)
        order.write({"shopify_exchange_key": exchange_key, "shopify_exchange_price_diff": 0.0})
        with self.assertRaises(UserError):
            self._wizard(
                order,
                [
                    {
                        "sale_line_id": sol.id,
                        "return_qty": 1,
                        "replacement_product_id": self.product_a.id,
                        "replacement_qty": 1,
                        "replacement_price_unit": 100.0,
                    }
                ],
            ).action_process_exchange()
        self.assertFalse(order.shopify_exchange_child_ids)

    def test_08_fail_credit_note_before_replacement(self):
        order = self._make_order(
            "ex-fail-cn",
            [(self.product_a, 1, 100.0, "L8")],
        )
        wiz = self._wizard(
            order,
            [
                {
                    "sale_line_id": order.order_line[0].id,
                    "return_qty": 1,
                    "replacement_product_id": self.product_a.id,
                    "replacement_qty": 1,
                    "replacement_price_unit": 100.0,
                }
            ],
        )
        with patch.object(
            RefundSyncService,
            "_create_partial_credit_note",
            side_effect=Exception("forced CN failure"),
        ):
            with self.assertRaises(Exception):
                wiz.action_process_exchange()
        self.assertFalse(order.shopify_exchange_child_ids)
        self.assertFalse(
            self.env["account.move"].search_count([("shopify_refund_id", "=", "exchange-%s" % order.id)])
        )
