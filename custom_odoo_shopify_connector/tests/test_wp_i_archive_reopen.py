"""WP-I — Shopify archive / unarchive / cancelled-order reopen (safe policy)."""
import json

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.order_archive_reopen_service import (
    REOPEN_ACTIVITY_SUMMARY,
    OrderArchiveReopenService,
)
from ..services import lifecycle_logger as llog


@tagged("post_install", "-at_install", "shopify_wp_i")
class TestWpIArchiveReopen(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.env["ir.config_parameter"].sudo().set_param("shopify.auto_heal_workflow", "False")

        cls.manager = cls.env.user
        cls.store = cls.env["shopify.store"].create(
            {
                "name": "WPI Store",
                "shop_url": "https://wpi.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "company_id": cls.company.id,
                "sync_archive_status": True,
                "sync_reopen_manual_review": True,
                "reopen_activity_user_id": cls.manager.id,
                "cancel_sync_mode": "cancel_only",
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "WPI Customer"})
        cls.product = cls.env["product.product"].create(
            {
                "name": "WPI Widget",
                "type": "consu",
                "list_price": 50.0,
                "taxes_id": [(6, 0, [])],
            }
        )
        cls.svc = OrderArchiveReopenService(cls.env)

    def _order(self, oid, state="sale", cancelled=False, confirm=False):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": str(oid),
                "shopify_instance_id": self.store.id,
                "shopify_cancelled": cancelled,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "name": self.product.display_name,
                "product_uom_qty": 1,
                "price_unit": 50.0,
            }
        )
        if confirm:
            order.action_confirm()
        if state == "cancel" or cancelled:
            if order.state != "cancel":
                try:
                    order.action_cancel()
                except Exception:
                    order.write({"state": "cancel"})
            order.write({"shopify_cancelled": True, "shopify_cancel_reason": "customer"})
        return order

    def _payload(self, oid, closed_at=None, cancelled_at=None, **extra):
        data = {
            "id": oid,
            "name": "#%s" % oid,
            "closed_at": closed_at,
            "cancelled_at": cancelled_at,
            "cancel_reason": "customer" if cancelled_at else None,
            "financial_status": "paid",
            "fulfillment_status": None,
            "updated_at": "2026-07-28T12:00:00-04:00",
            "total_price": "50.00",
        }
        data.update(extra)
        return data

    def _message_count(self, order):
        return self.env["mail.message"].search_count(
            [("model", "=", "sale.order"), ("res_id", "=", order.id)]
        )

    def _activity_count(self, order):
        return self.env["mail.activity"].search_count(
            [
                ("res_model", "=", "sale.order"),
                ("res_id", "=", order.id),
                ("summary", "=", REOPEN_ACTIVITY_SUMMARY),
            ]
        )

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------
    def test_01_active_becomes_archived(self):
        order = self._order("wpi-arch-1", confirm=True)
        state_before = order.state
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-arch-1", closed_at="2026-07-28T10:00:00Z")
        )
        self.assertTrue(order.shopify_is_archived)
        self.assertTrue(order.shopify_archived_at)
        self.assertEqual(order.state, state_before)

    def test_02_repeated_archive_idempotent(self):
        order = self._order("wpi-arch-2", confirm=True)
        payload = self._payload("wpi-arch-2", closed_at="2026-07-28T10:00:00Z")
        self.svc.apply_from_payload(self.store, order, payload)
        msgs = self._message_count(order)
        archived_at = order.shopify_archived_at
        self.svc.apply_from_payload(self.store, order, payload)
        self.svc.apply_from_payload(self.store, order, payload)
        self.assertTrue(order.shopify_is_archived)
        self.assertEqual(order.shopify_archived_at, archived_at)
        self.assertEqual(self._message_count(order), msgs)

    def test_03_archived_becomes_unarchived(self):
        order = self._order("wpi-arch-3", confirm=True)
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-arch-3", closed_at="2026-07-28T10:00:00Z")
        )
        state_before = order.state
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-arch-3", closed_at=None)
        )
        self.assertFalse(order.shopify_is_archived)
        self.assertFalse(order.shopify_archived_at)
        self.assertEqual(order.state, state_before)

    def test_04_archive_does_not_change_workflow_or_docs(self):
        order = self._order("wpi-arch-4", confirm=True)
        inv = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "invoice_origin": order.name,
            }
        )
        inv_state = inv.state
        inv_id = inv.id
        pick_states = order.picking_ids.mapped("state")
        so_state = order.state
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-arch-4", closed_at="2026-07-28T11:00:00Z")
        )
        self.assertEqual(order.state, so_state)
        inv = self.env["account.move"].browse(inv_id)
        self.assertEqual(inv.state, inv_state)
        self.assertEqual(order.picking_ids.mapped("state"), pick_states)
        self.assertFalse(order.shopify_refunded)
        self.assertTrue(order.shopify_is_archived)

    # ------------------------------------------------------------------
    # Reopen
    # ------------------------------------------------------------------
    def test_10_reopen_cancelled_flags_review_keeps_cancel(self):
        order = self._order("wpi-re-1", cancelled=True)
        self.assertEqual(order.state, "cancel")
        msgs_before = self._message_count(order)
        self.svc.apply_from_payload(
            self.store,
            order,
            self._payload("wpi-re-1", cancelled_at=None, closed_at=None),
        )
        self.assertEqual(order.state, "cancel")
        self.assertTrue(order.shopify_reopen_pending_review)
        self.assertTrue(order.shopify_reopened_at)
        self.assertTrue(order.shopify_reopen_payload)
        self.assertEqual(self._message_count(order), msgs_before + 1)
        self.assertEqual(self._activity_count(order), 1)
        self.assertTrue(order.shopify_cancelled)

    def test_11_repeated_reopen_no_duplicate_side_effects(self):
        order = self._order("wpi-re-2", cancelled=True)
        payload = self._payload("wpi-re-2", cancelled_at=None)
        self.svc.apply_from_payload(self.store, order, payload)
        msgs = self._message_count(order)
        acts = self._activity_count(order)
        reopened_at = order.shopify_reopened_at
        self.svc.apply_from_payload(self.store, order, payload)
        self.svc.apply_from_payload(self.store, order, payload)
        self.assertEqual(self._message_count(order), msgs)
        self.assertEqual(self._activity_count(order), acts)
        self.assertEqual(order.shopify_reopened_at, reopened_at)
        self.assertEqual(order.state, "cancel")

    def test_12_reopen_with_invoice_credit_does_not_alter_accounting(self):
        order = self._order("wpi-re-3", cancelled=True)
        inv = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "invoice_origin": order.name,
                "state": "posted" if False else "draft",
            }
        )
        # Keep draft to avoid chart-of-accounts requirements; assert identity.
        cn = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": self.partner.id,
                "invoice_origin": order.name,
            }
        )
        inv_write_date = inv.write_date
        cn_write_date = cn.write_date
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-re-3", cancelled_at=None)
        )
        self.assertEqual(order.state, "cancel")
        self.assertEqual(inv.state, "draft")
        self.assertEqual(cn.state, "draft")
        self.assertEqual(inv.write_date, inv_write_date)
        self.assertEqual(cn.write_date, cn_write_date)

    def test_13_reopen_with_delivery_does_not_alter_stock(self):
        order = self._order("wpi-re-4", confirm=True)
        # Cancel after confirm so pickings exist.
        try:
            order.action_cancel()
        except Exception:
            order.write({"state": "cancel"})
        order.write({"shopify_cancelled": True})
        pick_snapshot = [(p.id, p.state) for p in order.picking_ids]
        move_snapshot = [
            (m.id, m.state, m.quantity)
            for m in order.picking_ids.move_ids
        ]
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-re-4", cancelled_at=None)
        )
        self.assertEqual([(p.id, p.state) for p in order.picking_ids], pick_snapshot)
        self.assertEqual(
            [(m.id, m.state, m.quantity) for m in order.picking_ids.move_ids],
            move_snapshot,
        )
        self.assertEqual(order.state, "cancel")

    def test_14_mark_reopen_reviewed_clears_flag_only(self):
        order = self._order("wpi-re-5", cancelled=True)
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-re-5", cancelled_at=None)
        )
        self.assertTrue(order.shopify_reopen_pending_review)
        order.action_mark_shopify_reopen_reviewed()
        self.assertFalse(order.shopify_reopen_pending_review)
        self.assertEqual(order.state, "cancel")
        self.assertTrue(order.shopify_reopened_at)

    def test_15_create_replacement_quotation_once(self):
        order = self._order("wpi-re-6", cancelled=True)
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-re-6", cancelled_at=None)
        )
        action = order.action_create_shopify_reopen_replacement()
        replacement = self.env["sale.order"].browse(action["res_id"])
        self.assertEqual(replacement.state, "draft")
        self.assertEqual(replacement.shopify_reopen_original_id, order)
        self.assertEqual(order.shopify_reopen_replacement_id, replacement)
        self.assertFalse(replacement.shopify_order_id)
        self.assertEqual(len(replacement.order_line), 1)
        self.assertEqual(order.state, "cancel")
        with self.assertRaises(UserError):
            order.action_create_shopify_reopen_replacement()

    def test_16_queue_update_path_applies_archive(self):
        order = self._order("wpi-q-1", confirm=True)
        from ..services.order_service import OrderService
        from ..services.order_import_service import OrderImportService
        from ..services.order_update_service import OrderUpdateService
        from ..services.fulfillment_service import ShopifyFulfillmentService

        Queue = self.env["shopify.order.queue"]
        payload = self._payload("wpi-q-1", closed_at="2026-07-28T15:00:00Z")
        queue = Queue.create(
            {
                "store_id": self.store.id,
                "shopify_order_id": "wpi-q-1",
                "job_type": "update",
                "state": "pending",
                "payload": json.dumps(payload),
            }
        )
        import_service = OrderImportService(self.env)
        order_service = OrderService(self.env, import_service=import_service)
        update_service = OrderUpdateService(self.env, import_service=import_service)
        fulfillment_service = ShopifyFulfillmentService(self.env)
        trace = llog.LifecycleTrace(
            correlation_id="wpi-q-1",
            op=llog.OP_UPDATE,
            shop_order="wpi-q-1",
            queue=queue.id,
        )
        ok, msg = Queue._dispatch_queue(
            queue,
            payload,
            trace,
            import_service=import_service,
            order_service=order_service,
            update_service=update_service,
            fulfillment_service=fulfillment_service,
        )
        self.assertTrue(ok, msg)
        order.invalidate_recordset()
        self.assertTrue(order.shopify_is_archived)
        self.assertEqual(order.state, "sale")

    def test_17_store_flags_disable_archive_and_reopen(self):
        order = self._order("wpi-off-1", cancelled=True)
        self.store.sync_archive_status = False
        self.store.sync_reopen_manual_review = False
        self.svc.apply_from_payload(
            self.store,
            order,
            self._payload(
                "wpi-off-1",
                closed_at="2026-07-28T10:00:00Z",
                cancelled_at=None,
            ),
        )
        self.assertFalse(order.shopify_is_archived)
        self.assertFalse(order.shopify_reopen_pending_review)
        self.store.sync_archive_status = True
        self.store.sync_reopen_manual_review = True

    def test_18_local_cancel_without_shopify_flag_not_reopen(self):
        order = self._order("wpi-local-1", confirm=True)
        try:
            order.action_cancel()
        except Exception:
            order.write({"state": "cancel"})
        order.write({"shopify_cancelled": False})
        self.svc.apply_from_payload(
            self.store, order, self._payload("wpi-local-1", cancelled_at=None)
        )
        self.assertFalse(order.shopify_reopen_pending_review)
