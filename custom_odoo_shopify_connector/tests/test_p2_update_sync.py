"""P2 — Existing order update-sync routing tests (A-G)."""
import json

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services import lifecycle_logger as llog
from ..services.order_update_service import (
    STRATEGY_IN_PLACE,
    STRATEGY_ADJUSTMENT_REQUIRED,
    OrderUpdateService,
)


@tagged("post_install", "-at_install", "shopify_p2")
class TestP2UpdateSync(TransactionCase):
    def setUp(self):
        super().setUp()
        self.handler = self.env["shopify.webhook.handler"].sudo()
        self.queue_model = self.env["shopify.order.queue"].sudo()
        self.env["ir.config_parameter"].sudo().set_param("shopify.auto_heal_workflow", "False")
        self.noop_workflow = self.env["shopify.sale.auto.workflow"].sudo().create(
            {
                "name": "P2 Noop Workflow",
                "confirm_quotation": False,
                "create_invoice": False,
                "validate_invoice": False,
                "register_payment": False,
            }
        )
        self.partner = self.env["res.partner"].create({"name": "P2 Customer"})
        self.product = self.env["product.product"].create(
            {
                "name": "P2 Widget",
                "type": "consu",
                "list_price": 100.0,
                "default_code": "P2-SKU",
            }
        )
        income = self.env["account.account"].search(
            [("account_type", "=", "income")], limit=1
        )
        if income:
            self.product.property_account_income_id = income

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _store(self, mode="draft_sent"):
        return self.env["shopify.store"].sudo().create(
            {
                "name": "P2 Store %s" % mode,
                "shop_url": "https://p2-%s.myshopify.com" % mode,
                "access_token": "dummy",
                "active": True,
                "manage_orders_webhook": True,
                "auto_create_product_if_not_found": True,
                "order_edit_sync_mode": mode,
                "sale_auto_workflow_id": self.noop_workflow.id,
            }
        )

    def _linked_order(self, store, oid, line_id="L1", qty=2, price=100.0):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": oid,
                "shopify_instance_id": store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "product_uom_qty": qty,
                "price_unit": price,
                "shopify_line_item_id": line_id,
            }
        )
        return order

    def _payload(self, oid, line_id="L1", qty=5, price=100.0, name=None):
        return {
            "id": oid,
            "name": name or "#%s" % oid,
            "customer": {"id": 9901, "email": "p2@example.com", "first_name": "P", "last_name": "2"},
            "line_items": [
                {
                    "id": line_id,
                    "variant_id": 9001,
                    "sku": "P2-SKU",
                    "name": "P2 Widget",
                    "price": str(price),
                    "quantity": qty,
                }
            ],
            "total_price": str(price * qty),
        }

    def _count_orders(self, store, oid):
        return self.env["sale.order"].search_count(
            [("shopify_order_id", "=", oid), ("shopify_instance_id", "=", store.id)]
        )

    def _line(self, order):
        return order.order_line.filtered(lambda l: not l.display_type)[:1]

    # ------------------------------------------------------------------
    # Test A — Existing draft order update
    # ------------------------------------------------------------------
    def test_A_existing_draft_update_in_place(self):
        store = self._store(mode="draft_sent")
        order = self._linked_order(store, "p2-A", qty=2, price=100.0)
        self.assertEqual(order.state, "draft")

        strategy = OrderUpdateService(self.env).select_update_strategy(order, store)
        self.assertEqual(strategy, STRATEGY_IN_PLACE)

        self.handler.process_webhook_order(
            self._payload("p2-A", qty=5, price=120.0),
            store,
            operation=llog.OP_UPDATE,
            correlation_id="sh-A",
        )
        self.queue_model.process_queue()

        self.assertEqual(self._count_orders(store, "p2-A"), 1, "no duplicate SO")
        line = self._line(order)
        self.assertEqual(line.product_uom_qty, 5.0)
        self.assertEqual(line.price_unit, 120.0)

    # ------------------------------------------------------------------
    # Test B — Existing confirmed, not invoiced order
    # ------------------------------------------------------------------
    def test_B_existing_confirmed_not_invoiced_update(self):
        store = self._store(mode="confirmed")
        order = self._linked_order(store, "p2-B", qty=2, price=100.0)
        order.action_confirm()
        self.assertEqual(order.state, "sale")

        strategy = OrderUpdateService(self.env).select_update_strategy(order, store)
        self.assertEqual(strategy, STRATEGY_IN_PLACE)

        self.handler.process_webhook_order(
            self._payload("p2-B", qty=4, price=100.0),
            store,
            operation=llog.OP_UPDATE,
        )
        self.queue_model.process_queue()

        self.assertEqual(self._count_orders(store, "p2-B"), 1)
        self.assertEqual(self._line(order).product_uom_qty, 4.0)
        self.assertFalse(
            order.invoice_ids.filtered(lambda m: m.state == "posted"),
            "no posted invoice should have been created by an update",
        )

    # ------------------------------------------------------------------
    # Test C — Existing order with posted invoice (must be blocked)
    # ------------------------------------------------------------------
    def test_C_posted_invoice_update_is_blocked(self):
        store = self._store(mode="draft_sent")
        order = self._linked_order(store, "p2-C", qty=2, price=100.0)
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

        qty_before = self._line(order).product_uom_qty
        inv_total_before = invoice.amount_total
        inv_line_count_before = len(invoice.invoice_line_ids)

        strategy = OrderUpdateService(self.env).select_update_strategy(order, store)
        self.assertEqual(strategy, STRATEGY_ADJUSTMENT_REQUIRED)

        self.handler.process_webhook_order(
            self._payload("p2-C", qty=9, price=100.0),
            store,
            operation=llog.OP_UPDATE,
            correlation_id="sh-C",
        )
        self.queue_model.process_queue()

        # No duplicate order, posted invoice untouched, sale line NOT changed.
        self.assertEqual(self._count_orders(store, "p2-C"), 1)
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.amount_total, inv_total_before)
        self.assertEqual(len(invoice.invoice_line_ids), inv_line_count_before)
        self.assertEqual(self._line(order).product_uom_qty, qty_before)

        blocked_log = self.env["shopify.sync.log"].search(
            [("order_id", "=", "p2-C"), ("status", "=", "failed")], limit=1
        )
        self.assertTrue(blocked_log, "a blocked/adjustment-required log must be recorded")
        self.assertIn("adjustment", (blocked_log.message or "").lower())

    # ------------------------------------------------------------------
    # Test D — Duplicate CREATE
    # ------------------------------------------------------------------
    def test_D_duplicate_create_no_duplicate_so(self):
        store = self._store(mode="draft_sent")
        payload = self._payload("p2-D", qty=1, price=100.0)

        # Two create events before processing -> a single pending queue (dedup).
        self.handler.process_webhook_order(payload, store, operation=llog.OP_CREATE, correlation_id="sh-D1")
        self.handler.process_webhook_order(payload, store, operation=llog.OP_CREATE, correlation_id="sh-D2")
        pending = self.queue_model.search(
            [("shopify_order_id", "=", "p2-D"), ("store_id", "=", store.id), ("state", "=", "pending")]
        )
        self.assertEqual(len(pending), 1, "duplicate create must be de-duplicated at enqueue")

        self.queue_model.process_queue()
        self.assertEqual(self._count_orders(store, "p2-D"), 1)

        # A third create AFTER import must be idempotent (no new queue, no dup SO).
        self.handler.process_webhook_order(payload, store, operation=llog.OP_CREATE)
        still_pending = self.queue_model.search(
            [
                ("shopify_order_id", "=", "p2-D"),
                ("store_id", "=", store.id),
                ("state", "in", ["pending", "processing"]),
            ]
        )
        self.assertEqual(len(still_pending), 0, "create for existing order must be idempotent")
        self.queue_model.process_queue()
        self.assertEqual(self._count_orders(store, "p2-D"), 1)

    # ------------------------------------------------------------------
    # Test E — Duplicate UPDATE
    # ------------------------------------------------------------------
    def test_E_duplicate_update_is_idempotent(self):
        store = self._store(mode="draft_sent")
        self._linked_order(store, "p2-E", qty=2, price=100.0)

        self.handler.process_webhook_order(
            self._payload("p2-E", qty=5, price=100.0), store, operation=llog.OP_UPDATE
        )
        self.queue_model.process_queue()
        order = self.env["sale.order"].search(
            [("shopify_order_id", "=", "p2-E"), ("shopify_instance_id", "=", store.id)], limit=1
        )
        self.assertEqual(self._line(order).product_uom_qty, 5.0)

        # Same update again -> identical result, no duplicate.
        self.handler.process_webhook_order(
            self._payload("p2-E", qty=5, price=100.0), store, operation=llog.OP_UPDATE
        )
        self.queue_model.process_queue()
        self.assertEqual(self._count_orders(store, "p2-E"), 1)
        self.assertEqual(self._line(order).product_uom_qty, 5.0)

    # ------------------------------------------------------------------
    # Regression F/G — real reference orders (controlled, rolled back)
    # ------------------------------------------------------------------
    def _regression_check(self, shopify_order_id, corr):
        order = self.env["sale.order"].search(
            [("shopify_order_id", "=", shopify_order_id)], limit=1
        )
        if not order:
            self.skipTest("Reference order %s not present in this database" % shopify_order_id)
        store = order.shopify_instance_id
        self.assertTrue(store, "reference order must be linked to a store")
        store.sudo().write({"manage_orders_webhook": True})  # rolled back after test

        queue_rec = self.env["shopify.order.queue"].search(
            [("shopify_order_id", "=", shopify_order_id)], order="id desc", limit=1
        )
        if queue_rec and queue_rec.payload:
            payload = json.loads(queue_rec.payload)
        else:
            payload = {"id": shopify_order_id, "name": order.origin or order.name}

        # Existing order must be FOUND (update path), not create/skip.
        found = OrderUpdateService(self.env)._posted_invoices(order)
        posted_before = {inv.id: (inv.state, inv.amount_total) for inv in found}
        payment_before = self.env["account.payment"].search_count(
            [("partner_id", "=", order.commercial_partner_id.id)]
        )
        so_count_before = self.env["sale.order"].search_count(
            [("shopify_order_id", "=", shopify_order_id)]
        )

        self.handler.process_webhook_order(
            payload, store, operation=llog.OP_UPDATE, webhook_id=corr, correlation_id=corr
        )
        # An UPDATE queue item must have been created (not a create).
        upd_queue = self.env["shopify.order.queue"].search(
            [
                ("shopify_order_id", "=", shopify_order_id),
                ("store_id", "=", store.id),
                ("job_type", "=", "update"),
                ("correlation_id", "=", corr),
            ]
        )
        self.assertTrue(upd_queue, "update routing must enqueue an UPDATE job")

        self.queue_model.process_queue()

        # No duplicate SO.
        self.assertEqual(
            self.env["sale.order"].search_count([("shopify_order_id", "=", shopify_order_id)]),
            so_count_before,
        )
        # Posted invoices unchanged (never directly modified).
        for inv in found:
            self.assertEqual(inv.state, posted_before[inv.id][0])
            self.assertEqual(inv.amount_total, posted_before[inv.id][1])
        # No payment registered by an update.
        payment_after = self.env["account.payment"].search_count(
            [("partner_id", "=", order.commercial_partner_id.id)]
        )
        self.assertEqual(payment_after, payment_before)

    def test_F_regression_21573(self):
        self._regression_check("7077840158916", "sh-regr-21573")

    def test_G_regression_21000(self):
        self._regression_check("7042901049540", "sh-regr-21000")
