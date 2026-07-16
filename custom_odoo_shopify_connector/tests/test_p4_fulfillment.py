"""P4 — Shopify fulfillment -> Odoo delivery/picking tests.

Covers the approved matrix (14 cases) plus operation-routing and loop-prevention
proofs:

  * Full / partial / second-partial / multi-product / multi-shipment fulfillment.
  * Shopify fulfillment-id idempotency (duplicate replay is a no-op).
  * Unmappable fulfillment line fails safely (no unrelated stock delivered).
  * Cancelled-before-done and reversal-after-done handling (never auto-revert).
  * COD invoice-on-delivery (invoice posted, payment NOT registered).
  * Paymob paid + fulfillment (no second invoice / payment).
  * Loop prevention: Shopify-originated picking is not echoed back to Shopify.
  * #21573 (paid) and #21000 (COD) controlled regressions.

All tests run transactionally; nothing is written to live Shopify.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.order_import_service import OrderImportService
from ..services.order_service import OrderService
from ..services.fulfillment_service import ShopifyFulfillmentService


@tagged("post_install", "-at_install", "shopify_p4")
class TestP4Fulfillment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.env["ir.config_parameter"].sudo().set_param("shopify.auto_heal_workflow", "False")

        cls.income = cls.env["account.account"].search(
            [("account_type", "=", "income")], limit=1
        )
        cls.bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.cash_journal = cls.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls._variant_of = {}

        cls.store = cls.env["shopify.store"].create(
            {
                "name": "P4 Store",
                "shop_url": "https://p4.myshopify.com",
                "access_token": "dummy",
                "webhook_secret": "shh",
                "manage_orders_webhook": True,
                "active": True,
                "auto_create_product_if_not_found": True,
            }
        )

        cls.pA = cls._make_product("P4 Widget A", "P4-A", 8001)
        cls.pB = cls._make_product("P4 Widget B", "P4-B", 8002)

        Wf = cls.env["shopify.sale.auto.workflow"]
        cls.wf_online = Wf.create(
            {
                "name": "P4 Online Paid",
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
                "name": "P4 COD",
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
                "name": "P4 Confirm Only",
                "confirm_quotation": True,
                "create_invoice": False,
                "validate_invoice": False,
                "register_payment": False,
            }
        )
        cls.store.sale_auto_workflow_id = cls.wf_safe.id

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
        cls._map(cls.gw_paymob, "paid", cls.wf_online)
        cls._map(cls.gw_cod, "pending", cls.wf_cod)
        cls._map(cls.gw_cod, "paid", cls.wf_cod)

        cls.svc = OrderImportService(cls.env)
        cls.order_service = OrderService(cls.env, import_service=cls.svc)
        cls.fs = ShopifyFulfillmentService(cls.env)

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_product(cls, name, sku, variant_id):
        product = cls.env["product.product"].create(
            {
                "name": name,
                "type": "consu",
                "is_storable": True,
                "invoice_policy": "order",
                "list_price": 100.0,
                "default_code": sku,
                "taxes_id": [(6, 0, [])],
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
        cls._variant_of[product.id] = variant_id
        return product

    def _vid(self, product):
        return self._variant_of[product.id]

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

    def _add_stock(self, product, qty):
        self.env["stock.quant"]._update_available_quantity(
            product, self.stock_location, qty
        )

    def _item(self, product, qty=1, price=100.0):
        return {
            "id": 700000 + self._vid(product) + qty,
            "variant_id": self._vid(product),
            "sku": product.default_code,
            "name": product.name,
            "price": "%.2f" % price,
            "quantity": qty,
            "total_discount": "0.00",
            "taxable": False,
            "tax_lines": [],
        }

    def _order_payload(self, oid, items, gateway="Paymob", financial_status="paid",
                       transactions=None, total_price=0.0):
        return {
            "id": oid,
            "name": "#%s" % oid,
            "customer": {"id": 4440, "email": "p4@example.com",
                         "first_name": "P", "last_name": "4"},
            "financial_status": financial_status,
            "payment_gateway_names": [gateway],
            "taxes_included": False,
            "total_price": "%.2f" % total_price,
            "total_tax": "0.00",
            "total_discounts": "0.00",
            "line_items": items,
            "shipping_lines": [],
            "transactions": transactions or [],
        }

    def _import(self, oid, items, gateway="Paymob", financial_status="paid",
                transactions=None, total_price=0.0, stock_each=100):
        # ensure stock for every product referenced
        for it in items:
            product = self.env["product.product"].search(
                [("default_code", "=", it["sku"])], limit=1)
            self._add_stock(product, stock_each)
        payload = self._order_payload(
            oid, items, gateway=gateway, financial_status=financial_status,
            transactions=transactions, total_price=total_price)
        ok, err = self.svc.import_shopify_order(self.store, payload)
        self.assertTrue(ok, err)
        return self.env["sale.order"].search([("shopify_order_id", "=", oid)], limit=1)

    def _fulfillment(self, fid, lines, location_id=1, status=None):
        return {
            "id": fid,
            "location_id": location_id,
            "status": status,
            "line_items": [
                {"variant_id": self._vid(p), "sku": p.default_code, "quantity": q}
                for (p, q) in lines
            ],
        }

    def _fpayload(self, order_id, fulfillments, fulfillment_status):
        return {
            "id": order_id,
            "fulfillments": fulfillments,
            "fulfillment_status": fulfillment_status,
        }

    def _delivered(self, order, product):
        return sum(
            order.order_line.filtered(lambda l: l.product_id == product).mapped("qty_delivered")
        )

    # ==================================================================
    # Test 1 — Full fulfillment
    # ==================================================================
    def test_01_full_fulfillment(self):
        order = self._import("f1", [self._item(self.pA, qty=2)],
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "f1tx", "kind": "sale",
                                            "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        inv_before = len(order.invoice_ids)
        pay_before = self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)])

        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f1", [self._fulfillment(500, [(self.pA, 2)])], "fulfilled"))

        self.assertTrue(res["ok"])
        self.assertTrue(res["delivery_completed"])
        self.assertEqual(self._delivered(order, self.pA), 2.0)
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        # No duplicate SO / invoice / payment from a fulfillment event.
        self.assertEqual(len(self.env["sale.order"].search(
            [("shopify_order_id", "=", "f1")])), 1)
        self.assertEqual(len(order.invoice_ids), inv_before)
        self.assertEqual(self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)]), pay_before)

    # ==================================================================
    # Test 2 — Partial fulfillment
    # ==================================================================
    def test_02_partial_fulfillment(self):
        order = self._import("f2", [self._item(self.pA, qty=3)],
                             financial_status="paid",
                             transactions=[{"id": "f2tx", "kind": "sale",
                                            "status": "success", "amount": "300.00"}],
                             total_price=300.0)
        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f2", [self._fulfillment(510, [(self.pA, 1)])], "partial"))
        self.assertTrue(res["ok"])
        self.assertFalse(res["delivery_completed"], "2 of 3 still pending")
        self.assertEqual(self._delivered(order, self.pA), 1.0)
        # A backorder keeps the remaining 2 pending.
        self.assertTrue(any(p.state != "done" for p in order.picking_ids))

    # ==================================================================
    # Test 3 — Second partial fulfillment converges to full
    # ==================================================================
    def test_03_second_partial_reaches_full(self):
        order = self._import("f3", [self._item(self.pA, qty=3)],
                             financial_status="paid",
                             transactions=[{"id": "f3tx", "kind": "sale",
                                            "status": "success", "amount": "300.00"}],
                             total_price=300.0)
        self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f3", [self._fulfillment(520, [(self.pA, 1)])], "partial"))
        self.assertEqual(self._delivered(order, self.pA), 1.0)

        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f3", [self._fulfillment(521, [(self.pA, 2)])], "fulfilled"))
        self.assertTrue(res["delivery_completed"])
        self.assertEqual(self._delivered(order, self.pA), 3.0, "no double counting")
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))

    # ==================================================================
    # Test 4 — Duplicate fulfillment replay is idempotent
    # ==================================================================
    def test_04_duplicate_replay_idempotent(self):
        order = self._import("f4", [self._item(self.pA, qty=2)],
                             financial_status="paid",
                             transactions=[{"id": "f4tx", "kind": "sale",
                                            "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        payload = self._fpayload("f4", [self._fulfillment(530, [(self.pA, 2)])], "fulfilled")
        self.fs.handle_fulfillment(order, self.store, payload)
        ids_after = order.shopify_fulfillment_ids
        delivered_after = self._delivered(order, self.pA)
        pickings_after = len(order.picking_ids)

        res = self.fs.handle_fulfillment(order, self.store, payload)
        self.assertTrue(res["ok"])
        self.assertFalse(res["applied"], "duplicate must not re-apply")
        self.assertEqual(order.shopify_fulfillment_ids, ids_after)
        self.assertEqual(self._delivered(order, self.pA), delivered_after)
        self.assertEqual(len(order.picking_ids), pickings_after, "no duplicate picking")

    # ==================================================================
    # Test 5 — Multiple products, only selected fulfilled
    # ==================================================================
    def test_05_multiple_products_partial(self):
        order = self._import("f5", [self._item(self.pA, qty=2),
                                    self._item(self.pB, qty=2)],
                             financial_status="paid",
                             transactions=[{"id": "f5tx", "kind": "sale",
                                            "status": "success", "amount": "400.00"}],
                             total_price=400.0)
        self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f5", [self._fulfillment(540, [(self.pA, 2)])], "partial"))
        self.assertEqual(self._delivered(order, self.pA), 2.0)
        self.assertEqual(self._delivered(order, self.pB), 0.0, "B not fulfilled")

    # ==================================================================
    # Test 6 — Multiple shipments across events
    # ==================================================================
    def test_06_multiple_shipments(self):
        order = self._import("f6", [self._item(self.pA, qty=2),
                                    self._item(self.pB, qty=2)],
                             financial_status="paid",
                             transactions=[{"id": "f6tx", "kind": "sale",
                                            "status": "success", "amount": "400.00"}],
                             total_price=400.0)
        self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f6", [self._fulfillment(550, [(self.pA, 2)])], "partial"))
        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f6", [self._fulfillment(551, [(self.pB, 2)])], "fulfilled"))
        self.assertTrue(res["delivery_completed"])
        self.assertEqual(self._delivered(order, self.pA), 2.0)
        self.assertEqual(self._delivered(order, self.pB), 2.0)

    # ==================================================================
    # Test 7 — COD pending -> fulfilled -> invoice on delivery, no payment
    # ==================================================================
    def test_07_cod_invoice_on_delivery(self):
        order = self._import("f7", [self._item(self.pA, qty=1)],
                             gateway="cash_on_delivery", financial_status="pending",
                             total_price=100.0)
        self.assertEqual(order.state, "sale")
        self.assertFalse(order.invoice_ids.filtered(lambda m: m.state == "posted"),
                         "COD pending: no invoice before delivery")

        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f7", [self._fulfillment(560, [(self.pA, 1)])], "fulfilled"))
        self.assertTrue(res["delivery_completed"])
        self.svc.trigger_invoice_on_delivery(order, self.store)

        self.assertTrue(order.invoice_ids.filtered(lambda m: m.state == "posted"),
                        "COD invoice posted on completed delivery")
        self.assertFalse(
            self.env["account.payment"].search([("partner_id", "=", order.partner_id.id)]),
            "fulfillment completed != payment collected",
        )

    # ==================================================================
    # Test 8 — Paymob paid + fulfillment: no second invoice/payment
    # ==================================================================
    def test_08_paymob_paid_fulfillment_no_dup(self):
        order = self._import("f8", [self._item(self.pA, qty=1)],
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "f8tx", "kind": "sale",
                                            "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        inv_before = len(order.invoice_ids.filtered(lambda m: m.state == "posted"))
        pay_before = self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)])
        self.assertEqual(inv_before, 1)
        self.assertEqual(pay_before, 1)

        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f8", [self._fulfillment(570, [(self.pA, 1)])], "fulfilled"))
        self.svc.trigger_invoice_on_delivery(order, self.store)

        self.assertTrue(res["delivery_completed"])
        self.assertEqual(len(order.invoice_ids.filtered(lambda m: m.state == "posted")),
                         inv_before, "no second invoice")
        self.assertEqual(self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)]), pay_before, "no second payment")

    # ==================================================================
    # Test 9 — Unmappable fulfillment line fails safely
    # ==================================================================
    def test_09_unmappable_line_fails_safe(self):
        order = self._import("f9", [self._item(self.pA, qty=2)],
                             financial_status="paid",
                             transactions=[{"id": "f9tx", "kind": "sale",
                                            "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        bogus = {"id": 580, "location_id": 1, "status": None,
                 "line_items": [{"variant_id": 999999, "sku": "NOPE", "quantity": 1}]}
        res = self.fs.handle_fulfillment(
            order, self.store, self._fpayload("f9", [bogus], "partial"))
        self.assertTrue(res["mapping_failed"])
        self.assertTrue(order.shopify_fulfillment_mapping_failed)
        self.assertEqual(self._delivered(order, self.pA), 0.0, "no unrelated stock delivered")
        self.assertNotIn("580", order.shopify_fulfillment_ids or "")

    # ==================================================================
    # Test 10 — Cancelled fulfillment before done
    # ==================================================================
    def test_10_cancelled_before_done(self):
        order = self._import("f10", [self._item(self.pA, qty=2)],
                             financial_status="paid",
                             transactions=[{"id": "f10tx", "kind": "sale",
                                            "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f10", [self._fulfillment(590, [(self.pA, 2)], status="cancelled")],
                           "partial"))
        self.assertTrue(res["reversal"])
        self.assertFalse(res["ok"])
        self.assertEqual(self._delivered(order, self.pA), 0.0, "nothing delivered")
        self.assertFalse(order.shopify_fulfillment_reversal_flagged,
                         "no completed picking -> nothing to flag for return")

    # ==================================================================
    # Test 11 — Reversal after picking done (no auto-revert)
    # ==================================================================
    def test_11_reversal_after_done(self):
        order = self._import("f11", [self._item(self.pA, qty=2)],
                             financial_status="paid",
                             transactions=[{"id": "f11tx", "kind": "sale",
                                            "status": "success", "amount": "200.00"}],
                             total_price=200.0)
        self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f11", [self._fulfillment(600, [(self.pA, 2)])], "fulfilled"))
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))

        res = self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f11", [self._fulfillment(600, [(self.pA, 2)], status="cancelled")],
                           "partial"))
        self.assertTrue(res["reversal"])
        self.assertTrue(order.shopify_fulfillment_reversal_flagged)
        self.assertTrue(all(p.state == "done" for p in order.picking_ids),
                        "completed picking is never auto-reverted")

    # ==================================================================
    # Test 12 — Loop prevention (no echo back to Shopify)
    # ==================================================================
    def test_12_loop_prevention(self):
        order = self._import("f12", [self._item(self.pA, qty=1)],
                             financial_status="paid",
                             transactions=[{"id": "f12tx", "kind": "sale",
                                            "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        self.fs.handle_fulfillment(
            order, self.store,
            self._fpayload("f12", [self._fulfillment(610, [(self.pA, 1)])], "fulfilled"))
        picking = order.picking_ids.filtered(lambda p: p.state == "done")[:1]
        self.assertEqual(picking.shopify_fulfillment_origin, "shopify")
        self.assertEqual(picking.shopify_shipping_status, "done")
        # Give it tracking so it would otherwise be a push candidate.
        picking.carrier_tracking_ref = "TRK-LOOP"

        with patch(
            "odoo.addons.custom_odoo_shopify_connector.services."
            "shipping_service.ShopifyShippingService.update_shipping"
        ) as mocked:
            self.env["stock.picking"].cron_shopify_update_shipping_status()
            self.assertFalse(mocked.called,
                             "Shopify-originated picking must not be pushed back")

    # ==================================================================
    # Test 13 — #21573 controlled regression (paid) via queue routing
    # ==================================================================
    def test_13_regression_21573_via_queue(self):
        order = self._import("21573", [self._item(self.pA, qty=1, price=900.0)],
                             gateway="Paymob", financial_status="paid",
                             transactions=[{"id": "tx21573", "kind": "sale",
                                            "status": "success", "amount": "900.00"}],
                             total_price=900.0)
        inv_before = len(order.invoice_ids)
        pay_before = self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)])

        # Route a FULFILLMENT queue item (proves op routing, not CREATE workflow).
        import json
        queue = self.env["shopify.order.queue"].create(
            {
                "store_id": self.store.id,
                "shopify_order_id": "21573",
                "job_type": "fulfillment",
                "state": "pending",
                "payload": json.dumps(self._fpayload(
                    "21573", [self._fulfillment(620, [(self.pA, 1)])], "fulfilled")),
            }
        )
        self.env["shopify.order.queue"].process_queue()
        self.assertEqual(queue.state, "done")
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertEqual(len(self.env["sale.order"].search(
            [("shopify_order_id", "=", "21573")])), 1, "no duplicate SO")
        self.assertEqual(len(order.invoice_ids), inv_before, "invoice unchanged")
        self.assertEqual(self.env["account.payment"].search_count(
            [("partner_id", "=", order.partner_id.id)]), pay_before, "payment unchanged")

    # ==================================================================
    # Test 14 — #21000 COD regression: invoice on delivery, no payment
    # ==================================================================
    def test_14_regression_21000_cod_via_queue(self):
        order = self._import("21000", [self._item(self.pA, qty=1, price=483.92)],
                             gateway="cash_on_delivery", financial_status="pending",
                             total_price=483.92)
        self.assertFalse(order.invoice_ids.filtered(lambda m: m.state == "posted"))

        import json
        self.env["shopify.order.queue"].create(
            {
                "store_id": self.store.id,
                "shopify_order_id": "21000",
                "job_type": "fulfillment",
                "state": "pending",
                "payload": json.dumps(self._fpayload(
                    "21000", [self._fulfillment(630, [(self.pA, 1)])], "fulfilled")),
            }
        )
        self.env["shopify.order.queue"].process_queue()

        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
        self.assertTrue(order.invoice_ids.filtered(lambda m: m.state == "posted"),
                        "COD invoice-on-delivery posted via queue routing")
        self.assertFalse(
            self.env["account.payment"].search([("partner_id", "=", order.partner_id.id)]),
            "no payment without COD collection evidence",
        )

    # ==================================================================
    # Extra — webhook enqueues a fulfillment job (routing proof)
    # ==================================================================
    def test_15_webhook_enqueues_fulfillment_op(self):
        order = self._import("f15", [self._item(self.pA, qty=1)],
                             financial_status="paid",
                             transactions=[{"id": "f15tx", "kind": "sale",
                                            "status": "success", "amount": "100.00"}],
                             total_price=100.0)
        handler = self.env["shopify.webhook.handler"]
        handler.process_webhook_order(
            {"id": "f15", "order_id": "f15",
             "line_items": [{"variant_id": self._vid(self.pA),
                             "sku": self.pA.default_code, "quantity": 1}],
             "status": "success"},
            self.store,
            operation="fulfillment",
            webhook_id="wh-f15",
        )
        queue = self.env["shopify.order.queue"].search(
            [("shopify_order_id", "=", "f15"), ("job_type", "=", "fulfillment")], limit=1)
        self.assertTrue(queue, "fulfillment webhook enqueued a fulfillment job")
        self.env["shopify.order.queue"].process_queue()
        self.assertEqual(queue.state, "done")
        self.assertTrue(all(p.state == "done" for p in order.picking_ids))
