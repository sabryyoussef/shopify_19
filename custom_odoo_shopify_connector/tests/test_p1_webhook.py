"""P1 — Shopify webhook architecture & activation tests.

Covers the approved 22-case matrix at the handler/service layer (no live HTTP or
live Shopify writes). Webhooks converge into the same operation-aware lifecycle
router/services as polling; controllers are thin delegators over
`shopify.webhook.handler.ingest_webhook`.

Live steps (registration/activation against the real store, real inbound events)
are validated read-only in Step 1 and mocked here; live activation is gated on an
HTTPS callback + Test/UAT confirmation (see report).
"""
import base64
import hashlib
import hmac
import json

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.order_import_service import OrderImportService
from ..services.reconciliation_service import ShopifyReconciliationService
from ..services.webhook_registration_service import (
    DESIRED_TOPICS,
    ShopifyWebhookRegistrationService,
)


class _FakeRegClient:
    """Stub Shopify client for webhook registration/reconciliation tests."""

    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.created = []
        self.updated = []
        self.deleted = []

    def get_webhooks(self, **params):
        return list(self.existing)

    def create_webhook(self, topic, address, fmt="json"):
        wh = {"id": 1000 + len(self.created), "topic": topic, "address": address}
        self.created.append(wh)
        return wh

    def update_webhook(self, webhook_id, address, fmt="json"):
        self.updated.append((webhook_id, address))
        return {"id": webhook_id, "address": address}

    def delete_webhook(self, webhook_id):
        self.deleted.append(webhook_id)
        return {}


@tagged("post_install", "-at_install", "shopify_p1")
class TestP1Webhook(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.env["ir.config_parameter"].sudo().set_param("shopify.auto_heal_workflow", "False")

        cls.income = cls.env["account.account"].search([("account_type", "=", "income")], limit=1)
        cls.bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1)
        cls.cash_journal = cls.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", cls.company.id)], limit=1)
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls._variant_of = {}

        cls.store = cls.env["shopify.store"].create({
            "name": "P1 Store",
            "shop_url": "https://p1.myshopify.com",
            "access_token": "dummy",
            "webhook_secret": "p1secret",
            "manage_orders_webhook": True,
            "active": True,
            "auto_create_product_if_not_found": True,
            "import_order_status": "unshipped",
            "order_edit_sync_mode": "confirmed",
        })
        cls.pA = cls._make_product("P1 Widget A", "P1-A", 1101)

        Wf = cls.env["shopify.sale.auto.workflow"]
        cls.wf_online = Wf.create({
            "name": "P1 Online", "confirm_quotation": True, "create_invoice": True,
            "validate_invoice": True, "register_payment": True,
            "invoice_timing": "immediate", "require_payment_evidence": True})
        cls.wf_cod = Wf.create({
            "name": "P1 COD", "confirm_quotation": True, "create_invoice": True,
            "validate_invoice": True, "register_payment": True,
            "invoice_timing": "on_delivery", "require_payment_evidence": True})
        cls.wf_safe = Wf.create({
            "name": "P1 Safe", "confirm_quotation": True, "create_invoice": False,
            "validate_invoice": False, "register_payment": False})
        cls.store.sale_auto_workflow_id = cls.wf_safe.id

        Gw = cls.env["shopify.payment.gateway"]
        cls.gw_paymob = Gw.create({
            "name": "Paymob", "instance_id": cls.store.id, "payment_code": "Paymob",
            "gateway_type": "online", "odoo_journal_id": cls.bank_journal.id})
        cls.gw_cod = Gw.create({
            "name": "Cash on Delivery", "instance_id": cls.store.id,
            "payment_code": "cash_on_delivery", "gateway_type": "cod",
            "odoo_journal_id": cls.cash_journal.id})
        cls._map(cls.gw_paymob, "paid", cls.wf_online)
        cls._map(cls.gw_cod, "pending", cls.wf_cod)
        cls._map(cls.gw_cod, "paid", cls.wf_cod)

        cls.svc = OrderImportService(cls.env)
        cls.recon = ShopifyReconciliationService(cls.env)
        cls.handler = cls.env["shopify.webhook.handler"]

    # ---------------- fixtures ----------------
    @classmethod
    def _make_product(cls, name, sku, variant_id):
        product = cls.env["product.product"].create({
            "name": name, "type": "consu", "is_storable": True,
            "invoice_policy": "order", "list_price": 100.0, "default_code": sku,
            "taxes_id": [(6, 0, [])]})
        if cls.income:
            product.property_account_income_id = cls.income
        cls.env["shopify.variant.map"].create({
            "store_id": cls.store.id, "product_id": product.id,
            "shopify_product_id": str(variant_id * 10), "shopify_variant_id": str(variant_id)})
        cls._variant_of[product.id] = variant_id
        return product

    @classmethod
    def _map(cls, gateway, status, workflow):
        cls.env["shopify.financial.status"].create({
            "instance_id": cls.store.id, "payment_gateway_id": gateway.id,
            "shopify_financial_status": status, "workflow_id": workflow.id, "active": True})

    def _vid(self, product):
        return self._variant_of[product.id]

    def _add_stock(self, product, qty):
        self.env["stock.quant"]._update_available_quantity(product, self.stock_location, qty)

    def _item(self, product, qty=1, price=100.0):
        return {"id": 500000 + self._vid(product), "variant_id": self._vid(product),
                "sku": product.default_code, "name": product.name, "price": "%.2f" % price,
                "quantity": qty, "total_discount": "0.00", "taxable": False, "tax_lines": []}

    def _order(self, oid, items, updated_at="2026-01-01T10:00:00Z", gateway="Paymob",
               financial_status="paid", fulfillment_status=None, transactions=None, total_price=0.0):
        return {
            "id": oid, "name": "#%s" % oid,
            "customer": {"id": 1110, "email": "p1@example.com", "first_name": "P", "last_name": "1"},
            "created_at": "2026-01-01T09:00:00Z", "updated_at": updated_at,
            "financial_status": financial_status, "payment_gateway_names": [gateway],
            "fulfillment_status": fulfillment_status, "taxes_included": False,
            "total_price": "%.2f" % total_price, "total_tax": "0.00", "total_discounts": "0.00",
            "line_items": items, "shipping_lines": [], "transactions": transactions or []}

    def _ful_wh(self, fid, order_id, lines, status="success"):
        return {"id": fid, "order_id": order_id, "status": status,
                "line_items": [{"variant_id": self._vid(p), "sku": p.default_code, "quantity": q}
                               for (p, q) in lines]}

    def _import(self, oid, items, gateway="Paymob", financial_status="paid",
                transactions=None, total_price=0.0, stock_each=100):
        for it in items:
            product = self.env["product.product"].search([("default_code", "=", it["sku"])], limit=1)
            self._add_stock(product, stock_each)
        payload = self._order(oid, items, gateway=gateway, financial_status=financial_status,
                              transactions=transactions, total_price=total_price)
        ok, err = self.svc.import_shopify_order(self.store, payload)
        self.assertTrue(ok, err)
        return self.env["sale.order"].search([("shopify_order_id", "=", oid)], limit=1)

    # HMAC / ingest helpers
    def _raw(self, payload):
        return json.dumps(payload).encode("utf-8")

    def _sign(self, raw, secret=None):
        secret = secret or self.store.webhook_secret
        digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _ingest(self, payload, topic, webhook_id, hmac_header="__auto__", secret=None, store=None):
        store = store or self.store
        raw = self._raw(payload)
        if hmac_header == "__auto__":
            hmac_header = self._sign(raw, secret)
        return self.handler.ingest_webhook(
            store=store, raw_data=raw, hmac_header=hmac_header, webhook_id=webhook_id,
            topic=topic, shop_domain=store.shop_url)

    def _process(self):
        self.env["shopify.order.queue"].process_queue()

    def _delivered(self, order, product):
        return sum(order.order_line.filtered(lambda l: l.product_id == product).mapped("qty_delivered"))

    def _so(self, oid):
        return self.env["sale.order"].search([("shopify_order_id", "=", oid)], limit=1)

    def _events(self, webhook_id):
        return self.env["shopify.webhook.event"].search(
            [("store_id", "=", self.store.id), ("webhook_id", "=", webhook_id)])

    # ==================================================================
    # Test 1 — Valid CREATE webhook
    # ==================================================================
    def test_01_valid_create(self):
        self._add_stock(self.pA, 50)
        payload = self._order("w1", [self._item(self.pA, 1)],
                              transactions=[{"id": "w1tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                              total_price=100.0)
        res = self._ingest(payload, "orders/create", "wh-1")
        self.assertTrue(res["ok"])
        self.assertEqual(res["code"], 200)
        self.assertEqual(res["operation"], "create")
        self._process()
        self.assertEqual(len(self._so("w1")), 1)
        self.assertEqual(len(self._events("wh-1")), 1)

    # ==================================================================
    # Test 2 — Duplicate CREATE webhook (same delivery id)
    # ==================================================================
    def test_02_duplicate_create(self):
        self._add_stock(self.pA, 50)
        payload = self._order("w2", [self._item(self.pA, 1)],
                              transactions=[{"id": "w2tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                              total_price=100.0)
        self._ingest(payload, "orders/create", "wh-2")
        res2 = self._ingest(payload, "orders/create", "wh-2")
        self.assertTrue(res2["ok"])
        self.assertTrue(res2["duplicate"])
        self._process()
        self.assertEqual(len(self._so("w2")), 1, "no duplicate SO")
        self.assertEqual(len(self._events("wh-2")), 1, "single event row")

    # ==================================================================
    # Test 3 — Valid UPDATE webhook
    # ==================================================================
    def test_03_valid_update(self):
        order = self._import("w3", [self._item(self.pA, 1)], gateway="cash_on_delivery",
                             financial_status="pending", total_price=100.0)
        changed = self._order("w3", [self._item(self.pA, 4)], updated_at="2026-01-02T10:00:00Z",
                              gateway="cash_on_delivery", financial_status="pending", total_price=400.0)
        res = self._ingest(changed, "orders/updated", "wh-3")
        self.assertEqual(res["operation"], "update")
        self._process()
        line = order.order_line.filtered(lambda l: l.product_id == self.pA)[:1]
        self.assertEqual(line.product_uom_qty, 4.0)
        self.assertEqual(len(self._so("w3")), 1, "no CREATE rerun")

    # ==================================================================
    # Test 4 — Duplicate UPDATE webhook
    # ==================================================================
    def test_04_duplicate_update(self):
        self._import("w4", [self._item(self.pA, 1)], gateway="cash_on_delivery",
                     financial_status="pending", total_price=100.0)
        changed = self._order("w4", [self._item(self.pA, 2)], updated_at="2026-01-02T10:00:00Z",
                              gateway="cash_on_delivery", financial_status="pending", total_price=200.0)
        self._ingest(changed, "orders/updated", "wh-4")
        res2 = self._ingest(changed, "orders/updated", "wh-4")
        self.assertTrue(res2["duplicate"])
        q = self.env["shopify.order.queue"].search(
            [("store_id", "=", self.store.id), ("shopify_order_id", "=", "w4"), ("job_type", "=", "update")])
        self.assertEqual(len(q), 1, "duplicate update webhook did not create a second queue job")

    # ==================================================================
    # Test 5 — UPDATE webhook followed by polling (no dup)
    # ==================================================================
    def test_05_update_then_polling(self):
        order = self._import("w5", [self._item(self.pA, 1)], gateway="cash_on_delivery",
                             financial_status="pending", total_price=100.0)
        changed = self._order("w5", [self._item(self.pA, 3)], updated_at="2026-01-02T10:00:00Z",
                              gateway="cash_on_delivery", financial_status="pending", total_price=300.0)
        self._ingest(changed, "orders/updated", "wh-5")
        self._process()
        # Polling later sees the same updated_at as the now-advanced baseline.
        _existing, ops = self.recon.detect_operations(self.store, changed, allow_create=False)
        self.assertEqual(ops, [], "polling must not re-route an already-applied update")
        self.assertEqual(len(self._so("w5")), 1)

    # ==================================================================
    # Test 6 — Full FULFILLMENT webhook
    # ==================================================================
    def test_06_full_fulfillment(self):
        order = self._import("w6", [self._item(self.pA, 2)], gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "w6tx", "kind": "sale", "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        ful = self._ful_wh(7001, "w6", [(self.pA, 2)])
        res = self._ingest(ful, "fulfillments/create", "wh-6")
        self.assertEqual(res["operation"], "fulfillment")
        self._process()
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertEqual(self._delivered(order, self.pA), 2.0)

    # ==================================================================
    # Test 7 — Partial FULFILLMENT webhook
    # ==================================================================
    def test_07_partial_fulfillment(self):
        order = self._import("w7", [self._item(self.pA, 3)], gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "w7tx", "kind": "sale", "status": "success", "amount": "300.00"}],
                             total_price=300.0)
        ful = self._ful_wh(7002, "w7", [(self.pA, 1)])
        self._ingest(ful, "fulfillments/create", "wh-7")
        self._process()
        self.assertEqual(self._delivered(order, self.pA), 1.0, "only fulfilled qty delivered")
        self.assertTrue(any(p.state != "done" for p in order.picking_ids), "remaining pending")

    # ==================================================================
    # Test 8 — Duplicate FULFILLMENT webhook
    # ==================================================================
    def test_08_duplicate_fulfillment(self):
        order = self._import("w8", [self._item(self.pA, 2)], gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "w8tx", "kind": "sale", "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        ful = self._ful_wh(7003, "w8", [(self.pA, 2)])
        self._ingest(ful, "fulfillments/create", "wh-8")
        self._process()
        delivered = self._delivered(order, self.pA)
        res2 = self._ingest(ful, "fulfillments/create", "wh-8")
        self.assertTrue(res2["duplicate"])
        self._process()
        self.assertEqual(self._delivered(order, self.pA), delivered, "no double delivery")

    # ==================================================================
    # Test 9 — FULFILLMENT webhook followed by polling (no dup)
    # ==================================================================
    def test_09_fulfillment_then_polling(self):
        order = self._import("w9", [self._item(self.pA, 2)], gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "w9tx", "kind": "sale", "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        ful = self._ful_wh(7004, "w9", [(self.pA, 2)])
        self._ingest(ful, "fulfillments/create", "wh-9")
        self._process()
        delivered = self._delivered(order, self.pA)
        # Polling later returns the order carrying the same fulfillment id.
        polled = self._order("w9", [self._item(self.pA, 2)], updated_at="2026-01-01T10:00:00Z",
                             fulfillment_status="fulfilled")
        polled["fulfillments"] = [{"id": 7004, "location_id": 1, "status": None,
                                   "line_items": [{"variant_id": self._vid(self.pA), "quantity": 2}]}]
        _e, ops = self.recon.detect_operations(self.store, polled, allow_create=False)
        self.assertNotIn("fulfillment", [o for o, _r in ops], "already-processed fulfillment id not re-routed")
        self.assertEqual(self._delivered(order, self.pA), delivered)

    # ==================================================================
    # Test 10 — Cancellation webhook
    # ==================================================================
    def test_10_cancellation(self):
        order = self._import("w10", [self._item(self.pA, 1)], gateway="cash_on_delivery",
                             financial_status="pending", total_price=100.0)
        self.assertNotEqual(order.state, "cancel")
        payload = self._order("w10", [self._item(self.pA, 1)], gateway="cash_on_delivery",
                              financial_status="pending", total_price=100.0)
        payload["cancel_reason"] = "customer"
        res = self._ingest(payload, "orders/cancelled", "wh-10")
        self.assertEqual(res["operation"], "cancellation")
        self.assertTrue(res["ok"])
        self.assertEqual(order.state, "cancel")
        # No posted invoice existed, so nothing to reverse (safe).

    # ==================================================================
    # Test 11 — Refund webhook (accounting-safe credit note)
    # ==================================================================
    def test_11_refund(self):
        order = self._import("w11", [self._item(self.pA, 1, price=100.0)], gateway="Paymob",
                             financial_status="paid",
                             transactions=[{"id": "w11tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        posted = order.invoice_ids.filtered(lambda m: m.state == "posted")
        self.assertTrue(posted, "paid order should have a posted invoice")
        refund = {"id": 8001, "order_id": "w11", "note": "refund", "refund_line_items": []}
        res = self._ingest(refund, "refunds/create", "wh-11")
        self.assertEqual(res["operation"], "refund")
        credit = self.env["account.move"].search(
            [("move_type", "=", "out_refund"), ("shopify_refund_id", "=", "8001")])
        self.assertTrue(credit, "credit note created for refund")

    # ==================================================================
    # Test 12 — Duplicate refund webhook
    # ==================================================================
    def test_12_duplicate_refund(self):
        order = self._import("w12", [self._item(self.pA, 1, price=100.0)], gateway="Paymob",
                             financial_status="paid",
                             transactions=[{"id": "w12tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        refund = {"id": 8002, "order_id": "w12", "refund_line_items": []}
        self._ingest(refund, "refunds/create", "wh-12a")
        count1 = self.env["account.move"].search_count(
            [("move_type", "=", "out_refund"), ("shopify_refund_id", "=", "8002")])
        # Same refund id, different delivery id -> refund-id dedup blocks a 2nd CN.
        self._ingest(refund, "refunds/create", "wh-12b")
        count2 = self.env["account.move"].search_count(
            [("move_type", "=", "out_refund"), ("shopify_refund_id", "=", "8002")])
        self.assertEqual(count1, count2, "no duplicate credit note")
        self.assertEqual(count2, 1)

    # ==================================================================
    # Test 13 — Invalid HMAC rejected
    # ==================================================================
    def test_13_invalid_hmac(self):
        payload = self._order("w13", [self._item(self.pA, 1)])
        res = self.handler.ingest_webhook(
            store=self.store, raw_data=self._raw(payload), hmac_header="not-a-valid-signature",
            webhook_id="wh-13", topic="orders/create", shop_domain=self.store.shop_url)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 403)
        self.assertFalse(self._so("w13"))

    # ==================================================================
    # Test 14 — Missing HMAC rejected
    # ==================================================================
    def test_14_missing_hmac(self):
        payload = self._order("w14", [self._item(self.pA, 1)])
        res = self.handler.ingest_webhook(
            store=self.store, raw_data=self._raw(payload), hmac_header=None,
            webhook_id="wh-14", topic="orders/create", shop_domain=self.store.shop_url)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 403)

    # ==================================================================
    # Test 15 — Missing configured secret rejected
    # ==================================================================
    def test_15_missing_secret(self):
        store2 = self.env["shopify.store"].create({
            "name": "P1 No Secret", "shop_url": "https://p1nosecret.myshopify.com",
            "access_token": "dummy", "active": True, "manage_orders_webhook": True})
        payload = self._order("w15", [self._item(self.pA, 1)])
        raw = self._raw(payload)
        res = self.handler.ingest_webhook(
            store=store2, raw_data=raw, hmac_header=self._sign(raw, "whatever"),
            webhook_id="wh-15", topic="orders/create", shop_domain=store2.shop_url)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 403)
        self.assertIn("secret", (res.get("reason") or "").lower())

    # ==================================================================
    # Test 16 — Unsafe/failed processing surfaces safely (flagged, no stock)
    # ==================================================================
    def test_16_failed_processing_flagged_safely(self):
        # Unmappable fulfillment line -> no stock delivered, SO flagged for review,
        # queue item is an intentional no-op (no retry storm). The failure is
        # visible via the SO flag + sync log, not a poisoned retry loop.
        order = self._import("w16", [self._item(self.pA, 1)], gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "w16tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        bogus = {"id": 7016, "order_id": "w16", "status": "success",
                 "line_items": [{"variant_id": 999999, "sku": "NOPE", "quantity": 1}]}
        self._ingest(bogus, "fulfillments/create", "wh-16")
        self._process()
        self.assertTrue(order.shopify_fulfillment_mapping_failed, "SO flagged for manual review")
        self.assertEqual(self._delivered(order, self.pA), 0.0, "no unrelated stock delivered")
        # Webhook delivery is still persisted for audit/replay.
        self.assertTrue(self._events("wh-16"))

    # ==================================================================
    # Test 17 — Manual replay is idempotent
    # ==================================================================
    def test_17_manual_replay(self):
        self._add_stock(self.pA, 50)
        payload = self._order("w17", [self._item(self.pA, 1)],
                              transactions=[{"id": "w17tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                              total_price=100.0)
        self._ingest(payload, "orders/create", "wh-17")
        self._process()
        self.assertEqual(len(self._so("w17")), 1)
        event = self._events("wh-17")
        self.assertTrue(event)
        self.handler.replay_webhook_event(event)
        self._process()
        self.assertEqual(len(self._so("w17")), 1, "replay did not duplicate the SO")
        self.assertEqual(event.status, "processed")

    # ==================================================================
    # Test 18 — Registration reconciliation (missing topics)
    # ==================================================================
    def test_18_registration_reconcile(self):
        self.store.webhook_base_url = "https://erp.example.com"
        service = ShopifyWebhookRegistrationService(self.env)
        fake = _FakeRegClient(existing=[])
        report = service.reconcile(self.store, dry_run=False, client=fake)
        self.assertIsNone(report["blocked"])
        self.assertEqual(len(report["created"]), 6, "all desired topics registered")
        topics = {c["topic"] for c in report["created"]}
        self.assertIn("orders/create", topics)
        self.assertIn("fulfillments/create", topics)
        self.assertIn("refunds/create", topics)
        # Idempotent: a second reconcile with those now-existing creates nothing.
        fake2 = _FakeRegClient(existing=[
            {"id": i, "topic": t, "address": "https://erp.example.com%s" % p}
            for i, (t, p) in enumerate(DESIRED_TOPICS.items())])
        report2 = service.reconcile(self.store, dry_run=False, client=fake2)
        self.assertEqual(len(report2["created"]), 0)
        self.assertEqual(len(report2["updated"]), 0)

    # ==================================================================
    # Test 19 — Incorrect callback URL detected + repaired; https guard
    # ==================================================================
    def test_19_incorrect_url_repair_and_https_guard(self):
        self.store.webhook_base_url = "https://erp.example.com"
        service = ShopifyWebhookRegistrationService(self.env)
        fake = _FakeRegClient(existing=[
            {"id": 55, "topic": "orders/create", "address": "https://OLD.example.com/shopify/webhook/order"}])
        plan = service.plan(self.store, client=fake)
        self.assertTrue(any(i["topic"] == "orders/create" for i in plan["incorrect"]))
        report = service.reconcile(self.store, dry_run=False, client=fake)
        self.assertTrue(any(u[0] == 55 for u in fake.updated), "incorrect URL repaired")

        # https guard: non-https base blocks any live write.
        self.store.webhook_base_url = "http://insecure.example.com"
        blocked = service.reconcile(self.store, dry_run=False, client=_FakeRegClient())
        self.assertTrue(blocked["blocked"])
        self.assertEqual(len(blocked["created"]), 0)

    # ==================================================================
    # Test 20 — page_info pagination parsing (live validated read-only in Step 1)
    # ==================================================================
    def test_20_pagination_parsing(self):
        header = {"Link": '<https://x/admin/api/2025-01/orders.json?page_info=ABC123&limit=250>; rel="next"'}
        self.assertEqual(ShopifyReconciliationService._next_page_info(header), "ABC123")
        self.assertIsNone(ShopifyReconciliationService._next_page_info({}))

    # ==================================================================
    # Test 21 — #21573 webhook regression (paid)
    # ==================================================================
    def test_21_regression_21573(self):
        order = self._import("21573", [self._item(self.pA, 1, price=900.0)], gateway="Paymob",
                             financial_status="paid",
                             transactions=[{"id": "tx21573", "kind": "sale", "status": "success", "amount": "900.00"}],
                             total_price=900.0)
        inv_before = len(order.invoice_ids)
        pay_before = self.env["account.payment"].search_count([("partner_id", "=", order.partner_id.id)])
        ful = self._ful_wh(7573, "21573", [(self.pA, 1)])
        self._ingest(ful, "fulfillments/create", "wh-21573")
        self._process()
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertEqual(len(self._so("21573")), 1, "no duplicate SO")
        self.assertEqual(len(order.invoice_ids), inv_before, "invoice unchanged")
        self.assertEqual(self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)]), pay_before, "payment unchanged")

    # ==================================================================
    # Test 22 — #21000 COD webhook regression
    # ==================================================================
    def test_22_regression_21000_cod(self):
        order = self._import("21000", [self._item(self.pA, 1, price=483.92)], gateway="cash_on_delivery",
                             financial_status="pending", total_price=483.92)
        self.assertFalse(order.invoice_ids.filtered(lambda m: m.state == "posted"))
        ful = self._ful_wh(7000, "21000", [(self.pA, 1)])
        self._ingest(ful, "fulfillments/create", "wh-21000")
        self._process()
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertTrue(order.invoice_ids.filtered(lambda m: m.state == "posted"),
                        "COD invoice-on-delivery posted via webhook")
        self.assertFalse(self.env["account.payment"].search([("partner_id", "=", order.partner_id.id)]),
                         "no payment without COD collection evidence")
