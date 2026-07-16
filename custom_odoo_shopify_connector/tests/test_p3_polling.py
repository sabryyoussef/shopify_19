"""P3 — Polling fallback / reconciliation tests.

Covers the approved 14-case matrix. Polling only detects change and routes the
right lifecycle operation into the existing queue router/services; it never
duplicates CREATE/UPDATE/FULFILLMENT business logic.

All tests are transactional and use a fake Shopify API client (no live calls).
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.order_import_service import OrderImportService
from ..services.reconciliation_service import ShopifyReconciliationService


class _FakeAPI:
    """Minimal Shopify API stub with cursor pagination + optional failure."""

    def __init__(self, pages, raise_on_call=None):
        # pages: list of pages, each a list of order dicts
        self.pages = pages
        self.raise_on_call = raise_on_call
        self.calls = 0
        self.last_response_headers = {}

    def get_orders(self, **params):
        self.calls += 1
        if self.raise_on_call and self.calls == self.raise_on_call:
            raise Exception("boom page %s" % self.calls)
        idx = int(params.get("page_info", 0))
        orders = self.pages[idx] if idx < len(self.pages) else []
        if idx + 1 < len(self.pages):
            self.last_response_headers = {
                "Link": '<https://x/admin/api/2025-01/orders.json?page_info=%d&limit=250>; rel="next"'
                % (idx + 1)
            }
        else:
            self.last_response_headers = {}
        return list(orders)


@tagged("post_install", "-at_install", "shopify_p3")
class TestP3Polling(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.env["ir.config_parameter"].sudo().set_param("shopify.auto_heal_workflow", "False")

        cls.income = cls.env["account.account"].search(
            [("account_type", "=", "income")], limit=1)
        cls.bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1)
        cls.cash_journal = cls.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", cls.company.id)], limit=1)
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls._variant_of = {}

        cls.store = cls.env["shopify.store"].create(
            {
                "name": "P3 Store",
                "shop_url": "https://p3.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": True,
                "import_order_status": "unshipped",
                "order_edit_sync_mode": "confirmed",
                "poll_overlap_minutes": 10,
            }
        )
        cls.pA = cls._make_product("P3 Widget A", "P3-A", 3001)
        cls.pB = cls._make_product("P3 Widget B", "P3-B", 3002)

        Wf = cls.env["shopify.sale.auto.workflow"]
        cls.wf_online = Wf.create({
            "name": "P3 Online", "confirm_quotation": True, "create_invoice": True,
            "validate_invoice": True, "register_payment": True,
            "invoice_timing": "immediate", "require_payment_evidence": True})
        cls.wf_cod = Wf.create({
            "name": "P3 COD", "confirm_quotation": True, "create_invoice": True,
            "validate_invoice": True, "register_payment": True,
            "invoice_timing": "on_delivery", "require_payment_evidence": True})
        cls.wf_safe = Wf.create({
            "name": "P3 Safe", "confirm_quotation": True, "create_invoice": False,
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
            "shopify_product_id": str(variant_id * 10),
            "shopify_variant_id": str(variant_id)})
        cls._variant_of[product.id] = variant_id
        return product

    @classmethod
    def _map(cls, gateway, status, workflow):
        cls.env["shopify.financial.status"].create({
            "instance_id": cls.store.id, "payment_gateway_id": gateway.id,
            "shopify_financial_status": status, "workflow_id": workflow.id,
            "active": True})

    def _vid(self, product):
        return self._variant_of[product.id]

    def _add_stock(self, product, qty):
        self.env["stock.quant"]._update_available_quantity(product, self.stock_location, qty)

    def _item(self, product, qty=1, price=100.0):
        # Stable Shopify line-item id per product (independent of quantity) so
        # in-place updates match the existing order line.
        return {"id": 400000 + self._vid(product), "variant_id": self._vid(product),
                "sku": product.default_code, "name": product.name, "price": "%.2f" % price,
                "quantity": qty, "total_discount": "0.00", "taxable": False, "tax_lines": []}

    def _order(self, oid, items, updated_at, created_at="2026-01-01T09:00:00Z",
               gateway="Paymob", financial_status="paid", fulfillment_status=None,
               fulfillments=None, transactions=None, total_price=0.0):
        return {
            "id": oid, "name": "#%s" % oid,
            "customer": {"id": 3330, "email": "p3@example.com", "first_name": "P", "last_name": "3"},
            "created_at": created_at, "updated_at": updated_at,
            "financial_status": financial_status, "payment_gateway_names": [gateway],
            "fulfillment_status": fulfillment_status, "fulfillments": fulfillments or [],
            "taxes_included": False, "total_price": "%.2f" % total_price,
            "total_tax": "0.00", "total_discounts": "0.00",
            "line_items": items, "shipping_lines": [], "transactions": transactions or []}

    def _ful(self, fid, lines):
        return {"id": fid, "location_id": 1, "status": None,
                "line_items": [{"variant_id": self._vid(p), "sku": p.default_code, "quantity": q}
                               for (p, q) in lines]}

    def _import(self, oid, items, updated_at, gateway="Paymob", financial_status="paid",
                transactions=None, total_price=0.0, stock_each=100):
        for it in items:
            product = self.env["product.product"].search([("default_code", "=", it["sku"])], limit=1)
            self._add_stock(product, stock_each)
        payload = self._order(oid, items, updated_at, gateway=gateway,
                              financial_status=financial_status, transactions=transactions,
                              total_price=total_price)
        ok, err = self.svc.import_shopify_order(self.store, payload)
        self.assertTrue(ok, err)
        return self.env["sale.order"].search([("shopify_order_id", "=", oid)], limit=1)

    def _created(self, pages, raise_on_call=None):
        fake = _FakeAPI(pages, raise_on_call=raise_on_call)
        with patch.object(type(self.store), "_get_api_client", return_value=fake):
            self.recon.scan_created_orders(self.store)
        return fake

    def _updated(self, pages, raise_on_call=None):
        fake = _FakeAPI(pages, raise_on_call=raise_on_call)
        with patch.object(type(self.store), "_get_api_client", return_value=fake):
            self.recon.scan_updated_orders(self.store)
        return fake

    def _queues(self, oid, job_type=None):
        domain = [("store_id", "=", self.store.id), ("shopify_order_id", "=", str(oid))]
        if job_type:
            domain.append(("job_type", "=", job_type))
        return self.env["shopify.order.queue"].search(domain)

    def _process(self):
        self.env["shopify.order.queue"].process_queue()

    def _delivered(self, order, product):
        return sum(order.order_line.filtered(lambda l: l.product_id == product).mapped("qty_delivered"))

    # ==================================================================
    # Test 1 — New order detection (idempotent)
    # ==================================================================
    def test_01_new_order_detection(self):
        self._add_stock(self.pA, 50)
        order = self._order("p1", [self._item(self.pA, 1)], "2026-02-01T10:00:00Z",
                            gateway="Paymob", financial_status="paid",
                            transactions=[{"id": "p1tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                            total_price=100.0)
        self._created([[order]])
        self.assertEqual(len(self._queues("p1", "create")), 1, "one CREATE queued")
        self._process()
        self.assertEqual(len(self.env["sale.order"].search([("shopify_order_id", "=", "p1")])), 1)
        # Repeated poll: SO exists -> dedup, no new queue.
        self._created([[order]])
        self.assertEqual(len(self.env["sale.order"].search([("shopify_order_id", "=", "p1")])), 1)

    # ==================================================================
    # Test 2 / 3 — Existing order update recovery (missed webhook)
    # ==================================================================
    def test_02_existing_order_update(self):
        order = self._import("p2", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z",
                             gateway="cash_on_delivery", financial_status="pending", total_price=100.0)
        self.assertEqual(order.state, "sale")
        inv_before = len(order.invoice_ids)

        changed = self._order("p2", [self._item(self.pA, 3)], "2026-01-01T12:00:00Z",
                              gateway="cash_on_delivery", financial_status="pending", total_price=300.0)
        self._updated([[changed]])
        self.assertEqual(len(self._queues("p2", "update")), 1, "UPDATE queued")
        self.assertFalse(self._queues("p2", "create"), "no CREATE rerun")
        self._process()
        line = order.order_line.filtered(lambda l: l.product_id == self.pA)[:1]
        self.assertEqual(line.product_uom_qty, 3.0, "in-place update applied")
        self.assertEqual(len(order.invoice_ids), inv_before, "no duplicate invoice")

    def test_03_missed_webhook_recovery_uses_update(self):
        order = self._import("p3", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z",
                             gateway="cash_on_delivery", financial_status="pending", total_price=100.0)
        existing_before, ops = self.recon.detect_operations(
            self.store,
            self._order("p3", [self._item(self.pA, 2)], "2026-01-01T11:00:00Z",
                        gateway="cash_on_delivery", financial_status="pending"))
        self.assertEqual(existing_before, order)
        self.assertIn("update", [o for o, _r in ops])

    # ==================================================================
    # Test 4 — Fulfillment recovery via polling
    # ==================================================================
    def test_04_fulfillment_recovery(self):
        order = self._import("p4", [self._item(self.pA, 2)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "p4tx", "kind": "sale", "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        # Same updated_at (isolate FULFILLMENT), add a new fulfillment.
        polled = self._order("p4", [self._item(self.pA, 2)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             fulfillment_status="fulfilled", fulfillments=[self._ful(9100, [(self.pA, 2)])])
        self._updated([[polled]])
        self.assertEqual(len(self._queues("p4", "fulfillment")), 1, "FULFILLMENT queued")
        self.assertFalse(self._queues("p4", "update"), "updated_at unchanged -> no UPDATE")
        self._process()
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertEqual(self._delivered(order, self.pA), 2.0)

    # ==================================================================
    # Test 5 — Fulfilled order not blocked by unshipped import filter
    # ==================================================================
    def test_05_fulfilled_not_blocked_by_filter(self):
        self.assertEqual(self.store.import_order_status, "unshipped")
        order = self._import("p5", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "p5tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        polled = self._order("p5", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z",
                             fulfillment_status="fulfilled", fulfillments=[self._ful(9200, [(self.pA, 1)])])
        _existing, ops = self.recon.detect_operations(self.store, polled, allow_create=False)
        self.assertIn("fulfillment", [o for o, _r in ops],
                      "existing-order reconciliation must not be blocked by unshipped filter")

    # ==================================================================
    # Test 6 — Partial fulfillment via polling
    # ==================================================================
    def test_06_partial_fulfillment_via_polling(self):
        order = self._import("p6", [self._item(self.pA, 3)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "p6tx", "kind": "sale", "status": "success", "amount": "300.00"}],
                             total_price=300.0)
        polled = self._order("p6", [self._item(self.pA, 3)], "2026-01-01T10:00:00Z",
                             fulfillment_status="partial", fulfillments=[self._ful(9300, [(self.pA, 1)])])
        self._updated([[polled]])
        self._process()
        self.assertEqual(self._delivered(order, self.pA), 1.0)
        self.assertTrue(any(p.state != "done" for p in order.picking_ids), "remaining pending")

    # ==================================================================
    # Test 7 — Duplicate poll window (overlap) is idempotent
    # ==================================================================
    def test_07_duplicate_poll_window(self):
        order = self._import("p7", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "p7tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        polled = self._order("p7", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z",
                             fulfillment_status="fulfilled", fulfillments=[self._ful(9400, [(self.pA, 1)])])
        self._updated([[polled]])
        self._process()
        delivered = self._delivered(order, self.pA)
        # Overlap window returns same order again next run.
        self._updated([[polled]])
        self._process()
        self.assertEqual(self._delivered(order, self.pA), delivered, "no double fulfillment")
        self.assertEqual(len(self.env["sale.order"].search([("shopify_order_id", "=", "p7")])), 1)

    # ==================================================================
    # Test 8 — Pagination processes every page
    # ==================================================================
    def test_08_pagination(self):
        self._add_stock(self.pA, 50)
        o1 = self._order("p8a", [self._item(self.pA, 1)], "2026-02-02T10:00:00Z",
                         transactions=[{"id": "p8atx", "kind": "sale", "status": "success", "amount": "100.00"}],
                         total_price=100.0)
        o2 = self._order("p8b", [self._item(self.pA, 1)], "2026-02-02T11:00:00Z",
                         transactions=[{"id": "p8btx", "kind": "sale", "status": "success", "amount": "100.00"}],
                         total_price=100.0)
        o3 = self._order("p8c", [self._item(self.pA, 1)], "2026-02-02T12:00:00Z",
                         transactions=[{"id": "p8ctx", "kind": "sale", "status": "success", "amount": "100.00"}],
                         total_price=100.0)
        fake = self._created([[o1, o2], [o3]])
        self.assertEqual(fake.calls, 2, "both pages fetched")
        for oid in ("p8a", "p8b", "p8c"):
            self.assertEqual(len(self._queues(oid, "create")), 1, "page order %s enqueued" % oid)

    # ==================================================================
    # Test 9 — Failure before checkpoint (no advance past unprocessed)
    # ==================================================================
    def test_09_failure_before_checkpoint(self):
        self.store.last_order_import_time = False
        o1 = self._order("p9a", [self._item(self.pA, 1)], "2026-03-01T10:00:00Z")
        o2 = self._order("p9b", [self._item(self.pA, 1)], "2026-03-01T11:00:00Z")
        # page 1 ok, page 2 raises.
        self._created([[o1], [o2]], raise_on_call=2)
        self.assertFalse(self.store.last_order_import_time,
                         "checkpoint must not advance when a page fails")

    # ==================================================================
    # Test 10 — Retry next run stays idempotent
    # ==================================================================
    def test_10_retry_next_run_idempotent(self):
        self._add_stock(self.pA, 50)
        o1 = self._order("p10", [self._item(self.pA, 1)], "2026-03-05T10:00:00Z",
                         transactions=[{"id": "p10tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                         total_price=100.0)
        # First run fails on the (non-existent) page 2 after enqueuing p10.
        self._created([[o1], []], raise_on_call=2)
        self._process()
        self.assertEqual(len(self.env["sale.order"].search([("shopify_order_id", "=", "p10")])), 1)
        # Retry run reprocesses same range; idempotency prevents a duplicate.
        self._created([[o1]])
        self._process()
        self.assertEqual(len(self.env["sale.order"].search([("shopify_order_id", "=", "p10")])), 1)

    # ==================================================================
    # Test 11 — Operation-aware dedup (UPDATE + FULFILLMENT coexist)
    # ==================================================================
    def test_11_operation_aware_dedup(self):
        order = self._import("p11", [self._item(self.pA, 2)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "p11tx", "kind": "sale", "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        # Both updated_at bumped AND new fulfillment.
        polled = self._order("p11", [self._item(self.pA, 2)], "2026-01-01T12:00:00Z",
                             fulfillment_status="fulfilled", fulfillments=[self._ful(9500, [(self.pA, 2)])])
        self._updated([[polled]])
        self.assertEqual(len(self._queues("p11", "update")), 1, "UPDATE queued")
        self.assertEqual(len(self._queues("p11", "fulfillment")), 1,
                         "FULFILLMENT queued even though UPDATE exists")
        # Second poll before processing: both dedup-suppressed.
        self._updated([[polled]])
        self.assertEqual(len(self._queues("p11", "update")), 1, "duplicate UPDATE suppressed")
        self.assertEqual(len(self._queues("p11", "fulfillment")), 1, "duplicate FULFILLMENT suppressed")

    # ==================================================================
    # Test 12 — #21573 regression (paid) routes safely
    # ==================================================================
    def test_12_regression_21573(self):
        order = self._import("21573", [self._item(self.pA, 1, price=900.0)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "tx21573", "kind": "sale", "status": "success", "amount": "900.00"}],
                             total_price=900.0)
        inv_before = len(order.invoice_ids)
        pay_before = self.env["account.payment"].search_count([("partner_id", "=", order.partner_id.id)])
        polled = self._order("21573", [self._item(self.pA, 1, price=900.0)], "2026-01-01T10:00:00Z",
                             fulfillment_status="fulfilled", fulfillments=[self._ful(9600, [(self.pA, 1)])],
                             total_price=900.0)
        self._updated([[polled]])
        self.assertEqual(len(self._queues("21573", "fulfillment")), 1)
        self.assertFalse(self._queues("21573", "create"))
        self._process()
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertEqual(len(order.invoice_ids), inv_before, "invoice unchanged")
        self.assertEqual(self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)]), pay_before, "payment unchanged")

    # ==================================================================
    # Test 13 — #21000 COD regression: fulfillment via polling
    # ==================================================================
    def test_13_regression_21000_cod(self):
        order = self._import("21000", [self._item(self.pA, 1, price=483.92)], "2026-01-01T10:00:00Z",
                             gateway="cash_on_delivery", financial_status="pending", total_price=483.92)
        self.assertFalse(order.invoice_ids.filtered(lambda m: m.state == "posted"))
        polled = self._order("21000", [self._item(self.pA, 1, price=483.92)], "2026-01-01T10:00:00Z",
                             gateway="cash_on_delivery", financial_status="pending",
                             fulfillment_status="fulfilled", fulfillments=[self._ful(9700, [(self.pA, 1)])],
                             total_price=483.92)
        self._updated([[polled]])
        self._process()
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertTrue(order.invoice_ids.filtered(lambda m: m.state == "posted"),
                        "COD invoice-on-delivery posted via polling")
        self.assertFalse(self.env["account.payment"].search([("partner_id", "=", order.partner_id.id)]),
                         "no payment without COD collection evidence")

    # ==================================================================
    # Test 14 — No change -> skipped
    # ==================================================================
    def test_14_no_change_skipped(self):
        order = self._import("p14", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z",
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "p14tx", "kind": "sale", "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        polled = self._order("p14", [self._item(self.pA, 1)], "2026-01-01T10:00:00Z")
        _existing, ops = self.recon.detect_operations(self.store, polled, allow_create=False)
        self.assertEqual(ops, [], "nothing materially changed")
        self._updated([[polled]])
        self.assertFalse(self._queues("p14", "update"))
        self.assertFalse(self._queues("p14", "fulfillment"))
