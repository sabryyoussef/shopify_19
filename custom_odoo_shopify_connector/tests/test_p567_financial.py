"""P5/P6/P7 — Invoice workflow, payment sync and total reconciliation tests.

Covers the approved matrix:
  * P5 gateway + financial-status invoice decisioning (no aggressive fallback).
  * P6 safe payment registration + Shopify-transaction idempotency + journals.
  * P7 Shopify<->Odoo total reconciliation (tax-inclusive/exclusive, shipping,
    discounts) incl. #21573 / #21000 regressions.

All amounts are compared within a 0.01 rounding tolerance.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.order_import_service import OrderImportService
from ..services.order_service import OrderService

TOL = 0.01


@tagged("post_install", "-at_install", "shopify_p567")
class TestP567Financial(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.env["ir.config_parameter"].sudo().set_param("shopify.auto_heal_workflow", "False")

        Account = cls.env["account.account"]
        cls.income = Account.search([("account_type", "=", "income")], limit=1)

        cls.bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.cash_journal = cls.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", cls.company.id)], limit=1
        )

        # 10% customer tax the product defaults to — proves the double-tax fix:
        # even though the product carries a default 10% tax, a Shopify line whose
        # tax amount is 0 must NOT be taxed in Odoo.
        cls.tax10 = cls.env["account.tax"].create(
            {
                "name": "P567 VAT 10%",
                "amount_type": "percent",
                "amount": 10.0,
                "type_tax_use": "sale",
                "company_id": cls.company.id,
                "price_include_override": "tax_excluded",
            }
        )

        cls.delivery_product = cls.env["product.product"].create(
            {
                "name": "P567 Shipping",
                "type": "service",
                "invoice_policy": "order",
                "default_code": "P567-SHIP",
                "taxes_id": [(6, 0, [])],
            }
        )

        cls.store = cls.env["shopify.store"].create(
            {
                "name": "P567 Store",
                "shop_url": "https://p567.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": True,
                "shopify_tax_behavior": "create_tax_if_not_found",
                "delivery_product_id": cls.delivery_product.id,
                "company_id": cls.company.id,
            }
        )

        cls.product = cls._make_product("P567 Widget", "P567-SKU", 100.0, 9001)
        cls.product2 = cls._make_product("P567 Gadget", "P567-SKU2", 50.0, 9002)

        # Workflows
        Wf = cls.env["shopify.sale.auto.workflow"]
        cls.wf_online = Wf.create(
            {
                "name": "P567 Online Paid",
                "confirm_quotation": True,
                "create_invoice": True,
                "validate_invoice": True,
                "register_payment": True,
                "invoice_timing": "immediate",
                "require_payment_evidence": True,
            }
        )
        cls.wf_cod = Wf.create(
            {
                "name": "P567 COD",
                "confirm_quotation": True,
                "create_invoice": True,
                "validate_invoice": True,
                "register_payment": True,
                "invoice_timing": "on_delivery",
                "require_payment_evidence": True,
            }
        )
        cls.wf_safe = Wf.create(
            {
                "name": "P567 Confirm Only",
                "confirm_quotation": True,
                "create_invoice": False,
                "validate_invoice": False,
                "register_payment": False,
            }
        )
        cls.store.sale_auto_workflow_id = cls.wf_safe.id

        # Gateways
        Gw = cls.env["shopify.payment.gateway"]
        cls.gw_paymob = Gw.create(
            {
                "name": "Paymob",
                "instance_id": cls.store.id,
                "payment_code": "Paymob",
                "gateway_type": "online",
                "odoo_journal_id": cls.bank_journal.id,
            }
        )
        cls.gw_cod = Gw.create(
            {
                "name": "Cash on Delivery",
                "instance_id": cls.store.id,
                "payment_code": "cash_on_delivery",
                "gateway_type": "cod",
                "odoo_journal_id": cls.cash_journal.id,
            }
        )
        cls.gw_instapay = Gw.create(
            {
                "name": "InstaPay",
                "instance_id": cls.store.id,
                "payment_code": "instapay",
                "gateway_type": "bank",
                "odoo_journal_id": cls.bank_journal.id,
            }
        )
        # Known online gateway WITHOUT a journal -> payment must be skipped.
        cls.gw_nojournal = Gw.create(
            {
                "name": "Card No Journal",
                "instance_id": cls.store.id,
                "payment_code": "card_nojournal",
                "gateway_type": "online",
            }
        )

        # Financial-status mappings
        cls._map(cls.gw_paymob, "paid", cls.wf_online)
        cls._map(cls.gw_paymob, "partially_paid", cls.wf_online)
        cls._map(cls.gw_paymob, "pending", cls.wf_safe)
        cls._map(cls.gw_instapay, "paid", cls.wf_online)
        cls._map(cls.gw_nojournal, "paid", cls.wf_online)
        cls._map(cls.gw_cod, "paid", cls.wf_cod)
        cls._map(cls.gw_cod, "pending", cls.wf_cod)

        cls.svc = OrderImportService(cls.env)

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------
    @classmethod
    def _make_product(cls, name, sku, price, variant_id):
        product = cls.env["product.product"].create(
            {
                "name": name,
                "type": "consu",
                "is_storable": True,
                "list_price": price,
                "default_code": sku,
                "taxes_id": [(6, 0, cls.tax10.ids)],
            }
        )
        if cls.income:
            product.property_account_income_id = cls.income
        cls.env["shopify.variant.map"].create(
            {
                "store_id": cls.store.id,
                "product_id": product.id,
                "shopify_product_id": str(variant_id * 10),
                "shopify_variant_id": str(variant_id),
            }
        )
        return product

    @classmethod
    def _map(cls, gateway, status, workflow):
        cls.env["shopify.financial.status"].create(
            {
                "instance_id": cls.store.id,
                "payment_gateway_id": gateway.id,
                "shopify_financial_status": status,
                "workflow_id": workflow.id,
                "active": True,
            }
        )

    def _tax_line(self, amount, rate=0.1, title="GST"):
        return {"title": title, "rate": rate, "price": "%.2f" % amount}

    def _item(self, oid, variant_id=9001, sku="P567-SKU", price=100.0, qty=1,
              total_discount=0.0, tax_amount=0.0, taxable=False):
        return {
            "id": oid,
            "variant_id": variant_id,
            "sku": sku,
            "name": "Line %s" % oid,
            "price": "%.2f" % price,
            "quantity": qty,
            "total_discount": "%.2f" % total_discount,
            "taxable": taxable,
            "tax_lines": [self._tax_line(tax_amount)],
        }

    def _payload(self, oid, gateway="Paymob", financial_status="paid",
                 line_items=None, shipping_lines=None, total_price=0.0,
                 total_tax=0.0, total_discounts=0.0, taxes_included=False,
                 transactions=None):
        return {
            "id": oid,
            "name": "#%s" % oid,
            "customer": {"id": 5551, "email": "p567@example.com",
                         "first_name": "P", "last_name": "567"},
            "financial_status": financial_status,
            "payment_gateway_names": [gateway],
            "taxes_included": taxes_included,
            "total_price": "%.2f" % total_price,
            "total_tax": "%.2f" % total_tax,
            "total_discounts": "%.2f" % total_discounts,
            "line_items": line_items or [],
            "shipping_lines": shipping_lines or [],
            "transactions": transactions or [],
        }

    def _ship(self, price, tax_amount=0.0, is_removed=False, title="Standard"):
        return {
            "title": title,
            "price": "%.2f" % price,
            "discounted_price": "%.2f" % price,
            "is_removed": is_removed,
            "tax_lines": [self._tax_line(tax_amount)] if tax_amount else [],
        }

    def _build_so(self, payload):
        """Create the SO only (no workflow) — enough for total reconciliation."""
        return OrderService(self.env, import_service=self.svc).create_order_from_payload(
            payload, self.store
        )

    def _draft_order(self):
        return self.env["sale.order"].create(
            {"partner_id": self.env["res.partner"].create({"name": "D"}).id}
        )

    # ==================================================================
    # P7 — Total reconciliation
    # ==================================================================
    def test_15_no_discount_no_tax(self):
        p = self._payload("t15", line_items=[self._item("l1", price=100.0, qty=2)],
                          total_price=200.0)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_total, 200.0, delta=TOL)
        self.assertAlmostEqual(so.amount_tax, 0.0, delta=TOL,
                               msg="Shopify charged no tax -> Odoo must add none")

    def test_16_coupon_discount(self):
        item = self._item("l1", price=100.0, qty=1, total_discount=25.0)
        p = self._payload("t16", line_items=[item], total_price=75.0,
                          total_discounts=25.0)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_total, 75.0, delta=TOL)

    def test_17_order_level_discount_distributed(self):
        items = [self._item("l1", price=100.0, qty=1),
                 self._item("l2", variant_id=9002, sku="P567-SKU2", price=50.0, qty=1)]
        # No per-line discount, but order-level total_discounts must be distributed.
        p = self._payload("t17", line_items=items, total_price=120.0,
                          total_discounts=30.0)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_total, 120.0, delta=TOL)

    def test_18_full_discount_line(self):
        item = self._item("l1", price=900.0, qty=1, total_discount=900.0)
        p = self._payload("t18", line_items=[item], total_price=0.0,
                          total_discounts=900.0)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_total, 0.0, delta=TOL)

    def test_19_multiple_products(self):
        items = [self._item("l1", price=100.0, qty=2),
                 self._item("l2", variant_id=9002, sku="P567-SKU2", price=50.0, qty=3)]
        p = self._payload("t19", line_items=items, total_price=350.0)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_total, 350.0, delta=TOL)

    def test_20_shipping_charge(self):
        p = self._payload("t20", line_items=[self._item("l1", price=100.0, qty=1)],
                          shipping_lines=[self._ship(70.0)], total_price=170.0)
        so = self._build_so(p)
        ship_lines = so.order_line.filtered(
            lambda l: l.product_id.id == self.delivery_product.id
        )
        self.assertEqual(len(ship_lines), 1, "shipping line created")
        self.assertAlmostEqual(ship_lines.price_subtotal, 70.0, delta=TOL)
        self.assertAlmostEqual(so.amount_total, 170.0, delta=TOL)

    def test_21_free_shipping(self):
        p = self._payload("t21", line_items=[self._item("l1", price=100.0, qty=1)],
                          shipping_lines=[self._ship(0.0)], total_price=100.0)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_total, 100.0, delta=TOL)

    def test_21b_removed_shipping_skipped(self):
        p = self._payload("t21b", line_items=[self._item("l1", price=100.0, qty=1)],
                          shipping_lines=[self._ship(70.0, is_removed=True)],
                          total_price=100.0)
        so = self._build_so(p)
        ship_lines = so.order_line.filtered(
            lambda l: l.product_id.id == self.delivery_product.id
        )
        self.assertFalse(ship_lines, "removed shipping line must be skipped")
        self.assertAlmostEqual(so.amount_total, 100.0, delta=TOL)

    def test_22_tax_inclusive(self):
        # taxes_included=True, real tax charged. Line price already includes tax.
        item = self._item("l1", price=110.0, qty=1, tax_amount=10.0, taxable=True)
        p = self._payload("t22", line_items=[item], total_price=110.0,
                          total_tax=10.0, taxes_included=True)
        so = self._build_so(p)
        line = so.order_line.filtered(lambda l: not l.display_type)[:1]
        self.assertTrue(line.tax_ids, "inclusive tax applied")
        self.assertTrue(all(t.price_include for t in line.tax_ids))
        self.assertAlmostEqual(so.amount_total, 110.0, delta=TOL)

    def test_23_tax_exclusive(self):
        item = self._item("l1", price=100.0, qty=1, tax_amount=10.0, taxable=True)
        p = self._payload("t23", line_items=[item], total_price=110.0,
                          total_tax=10.0, taxes_included=False)
        so = self._build_so(p)
        line = so.order_line.filtered(lambda l: not l.display_type)[:1]
        self.assertTrue(line.tax_ids, "exclusive tax applied")
        self.assertAlmostEqual(so.amount_tax, 10.0, delta=TOL)
        self.assertAlmostEqual(so.amount_total, 110.0, delta=TOL)

    def test_24_regression_21573(self):
        # Real shape: 2 lines @900, one 100% discounted, GST rate 0.1 price 0,
        # shipping 70, total_tax 0 -> Shopify total 970.
        items = [
            self._item("l1", price=900.0, qty=1, total_discount=900.0),
            self._item("l2", variant_id=9002, sku="P567-SKU2", price=900.0, qty=1),
        ]
        p = self._payload("21573", line_items=items, shipping_lines=[self._ship(70.0)],
                          total_price=970.0, total_tax=0.0, total_discounts=900.0)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_tax, 0.0, delta=TOL,
                               msg="no VAT should be added (Shopify total_tax=0)")
        self.assertAlmostEqual(so.amount_total, 970.0, delta=TOL,
                               msg="Odoo total must equal Shopify 970 (was 990)")

    def test_25_regression_21000(self):
        # 1 line @990 discounted to 403.92 (59.2%), shipping 80, tax 0 -> 483.92.
        items = [self._item("l1", price=990.0, qty=1, total_discount=586.08)]
        p = self._payload("21000", line_items=items, shipping_lines=[self._ship(80.0)],
                          total_price=483.92, total_tax=0.0, total_discounts=586.08)
        so = self._build_so(p)
        self.assertAlmostEqual(so.amount_tax, 0.0, delta=TOL)
        self.assertAlmostEqual(so.amount_total, 483.92, delta=TOL,
                               msg="Odoo total must equal Shopify 483.92 (was 444.31)")

    # ==================================================================
    # P5 — Invoice decisioning (deterministic decision layer)
    # ==================================================================
    def _decide(self, payload, workflow):
        return self.svc.decide_financial_actions(self._draft_order(), self.store, payload, workflow)

    def test_01_paymob_paid_invoice_and_pay(self):
        p = self._payload("d1", gateway="Paymob", financial_status="paid",
                          transactions=[{"id": "tx1", "kind": "sale",
                                         "status": "success", "amount": "100.00"}])
        plan = self._decide(p, self.wf_online)
        self.assertEqual(plan["category"], "online")
        self.assertTrue(plan["create_invoice"])
        self.assertTrue(plan["post_invoice"])
        self.assertTrue(plan["register_payment"])
        self.assertEqual(plan["journal"], self.bank_journal)

    def test_02_paymob_pending_no_payment(self):
        p = self._payload("d2", gateway="Paymob", financial_status="pending")
        plan = self._decide(p, self.wf_online)
        self.assertFalse(plan["create_invoice"])
        self.assertFalse(plan["register_payment"])
        self.assertIn("uncaptured", plan["reason"])

    def test_03_cod_pending_confirm_only(self):
        p = self._payload("d3", gateway="cash_on_delivery", financial_status="pending")
        plan = self._decide(p, self.wf_cod)
        self.assertEqual(plan["category"], "cod")
        self.assertTrue(plan["confirm"])
        self.assertFalse(plan["create_invoice"], "COD pending: no premature invoice")
        self.assertFalse(plan["register_payment"], "COD pending: no payment")

    def test_04_cod_paid_not_delivered_awaits(self):
        p = self._payload("d4", gateway="cash_on_delivery", financial_status="paid")
        plan = self._decide(p, self.wf_cod)  # draft order -> no completed delivery
        self.assertFalse(plan["create_invoice"], "COD invoice waits for delivery")
        self.assertIn("await_delivery", plan["reason"])

    def test_05_instapay_paid_bank_journal(self):
        p = self._payload("d5", gateway="instapay", financial_status="paid",
                          transactions=[{"id": "tx5", "kind": "sale",
                                         "status": "success", "amount": "100.00"}])
        plan = self._decide(p, self.wf_online)
        self.assertEqual(plan["category"], "bank")
        self.assertTrue(plan["register_payment"])
        self.assertEqual(plan["journal"], self.bank_journal)

    def test_06_partially_paid(self):
        p = self._payload("d6", gateway="Paymob", financial_status="partially_paid",
                          transactions=[{"id": "tx6", "kind": "sale",
                                         "status": "success", "amount": "40.00"}])
        plan = self._decide(p, self.wf_online)
        self.assertTrue(plan["register_payment"])
        self.assertEqual(plan["reason"].split()[0], "prepaid_partial")

    def test_07_failed_no_payment(self):
        p = self._payload("d7", gateway="Paymob", financial_status="voided")
        plan = self._decide(p, self.wf_online)
        self.assertFalse(plan["create_invoice"])
        self.assertFalse(plan["register_payment"])

    def test_08_refunded_no_new_invoice(self):
        p = self._payload("d8", gateway="Paymob", financial_status="refunded")
        plan = self._decide(p, self.wf_online)
        self.assertFalse(plan["create_invoice"])
        self.assertFalse(plan["register_payment"])

    def test_09_unknown_gateway_safe(self):
        p = self._payload("d9", gateway="some_random_gw", financial_status="paid")
        plan = self._decide(p, self.wf_online)
        self.assertEqual(plan["category"], "unknown")
        self.assertTrue(plan["safe_fallback"])
        self.assertTrue(plan["config_error"])
        self.assertFalse(plan["register_payment"])
        self.assertTrue(plan["confirm"])

    def test_10_known_gateway_no_journal_skips_payment(self):
        p = self._payload("d10", gateway="card_nojournal", financial_status="paid",
                          transactions=[{"id": "tx10", "kind": "sale",
                                         "status": "success", "amount": "100.00"}])
        plan = self._decide(p, self.wf_online)
        self.assertTrue(plan["create_invoice"], "invoice still allowed")
        self.assertFalse(plan["register_payment"], "no journal -> no payment")
        self.assertTrue(plan["config_error"])
        self.assertIn("no_journal", plan["reason"])

    def test_11_paid_but_no_evidence_skips_payment(self):
        # financial_status implies settlement, but require_payment_evidence + a
        # status that carries no evidence (authorized) must not pay.
        p = self._payload("d11", gateway="Paymob", financial_status="authorized")
        plan = self._decide(p, self.wf_online)
        self.assertFalse(plan["register_payment"])

    # ==================================================================
    # P6 — End-to-end payment registration + idempotency
    # ==================================================================
    def test_12_paymob_paid_end_to_end_and_idempotent(self):
        txn = [{"id": "tx-e2e-1", "kind": "sale", "status": "success", "amount": "200.00"}]
        p = self._payload("e2e1", gateway="Paymob", financial_status="paid",
                          line_items=[self._item("l1", price=100.0, qty=2)],
                          total_price=200.0, transactions=txn)
        ok, err = self.svc.import_shopify_order(self.store, p)
        self.assertTrue(ok, err)
        order = self.env["sale.order"].search(
            [("shopify_order_id", "=", "e2e1")], limit=1)
        self.assertEqual(order.state, "sale")
        posted = order.invoice_ids.filtered(lambda m: m.state == "posted")
        self.assertTrue(posted, "invoice posted for paid online order")
        payments = self.env["account.payment"].search(
            [("shopify_transaction_id", "=", "tx-e2e-1")])
        self.assertEqual(len(payments), 1, "exactly one payment registered")
        self.assertEqual(payments.journal_id, self.bank_journal)

        # Idempotent replay: same transaction must not create a second payment.
        self.svc.import_shopify_order(self.store, p)
        payments2 = self.env["account.payment"].search(
            [("shopify_transaction_id", "=", "tx-e2e-1")])
        self.assertEqual(len(payments2), 1, "duplicate transaction -> no dup payment")

    def test_13_cod_pending_end_to_end_no_invoice_no_payment(self):
        p = self._payload("e2e2", gateway="cash_on_delivery", financial_status="pending",
                          line_items=[self._item("l1", price=100.0, qty=1)],
                          total_price=100.0)
        ok, err = self.svc.import_shopify_order(self.store, p)
        self.assertTrue(ok, err)
        order = self.env["sale.order"].search(
            [("shopify_order_id", "=", "e2e2")], limit=1)
        self.assertEqual(order.state, "sale", "COD confirmed")
        self.assertFalse(
            order.invoice_ids.filtered(lambda m: m.state == "posted"),
            "COD pending must not post an invoice",
        )
        self.assertFalse(self.env["account.payment"].search(
            [("partner_id", "=", order.partner_id.id)]),
            "COD pending must not register payment")
