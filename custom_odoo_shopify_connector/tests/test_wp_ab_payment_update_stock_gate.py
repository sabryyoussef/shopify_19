"""WP-A / WP-B — payment sync on order update + stock gate before confirm."""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.order_import_service import OrderImportService
from ..services import lifecycle_logger as llog
from ..services.order_service import OrderService
from ..services.order_update_service import OrderUpdateService
from ..services.fulfillment_service import ShopifyFulfillmentService


@tagged("post_install", "-at_install", "shopify_wp_ab")
class TestWpABPaymentUpdateStockGate(TransactionCase):
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

        cls.delivery_product = cls.env["product.product"].create(
            {
                "name": "WPAB Shipping",
                "type": "service",
                "invoice_policy": "order",
                "default_code": "WPAB-SHIP",
                "taxes_id": [(6, 0, [])],
            }
        )
        cls.store = cls.env["shopify.store"].create(
            {
                "name": "WPAB Store",
                "shop_url": "https://wpab.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": True,
                "shopify_tax_behavior": "create_tax_if_not_found",
                "delivery_product_id": cls.delivery_product.id,
                "company_id": cls.company.id,
                "confirm_require_stock": False,
                "partial_payment_mode": "register_paid_amount",
            }
        )

        Wf = cls.env["shopify.sale.auto.workflow"]
        cls.wf_paid = Wf.create(
            {
                "name": "WPAB Online Paid",
                "confirm_quotation": True,
                "create_invoice": True,
                "validate_invoice": True,
                "register_payment": True,
                "invoice_timing": "immediate",
                "require_payment_evidence": True,
            }
        )
        cls.wf_pending = Wf.create(
            {
                "name": "WPAB Pending",
                "confirm_quotation": True,
                "create_invoice": False,
                "validate_invoice": False,
                "register_payment": False,
            }
        )
        cls.store.sale_auto_workflow_id = cls.wf_pending.id

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
        Fin = cls.env["shopify.financial.status"]
        Fin.create(
            {
                "instance_id": cls.store.id,
                "payment_gateway_id": cls.gw_paymob.id,
                "shopify_financial_status": "paid",
                "workflow_id": cls.wf_paid.id,
                "active": True,
            }
        )
        Fin.create(
            {
                "instance_id": cls.store.id,
                "payment_gateway_id": cls.gw_paymob.id,
                "shopify_financial_status": "pending",
                "workflow_id": cls.wf_pending.id,
                "active": True,
            }
        )
        Fin.create(
            {
                "instance_id": cls.store.id,
                "payment_gateway_id": cls.gw_paymob.id,
                "shopify_financial_status": "partially_paid",
                "workflow_id": cls.wf_paid.id,
                "active": True,
            }
        )

        cls.product = cls.env["product.product"].create(
            {
                "name": "WPAB Widget",
                "type": "consu",
                "is_storable": True,
                "list_price": 100.0,
                "default_code": "WPAB-SKU",
                "taxes_id": [(6, 0, [])],
            }
        )
        if cls.income:
            cls.product.property_account_income_id = cls.income
        cls.env["shopify.variant.map"].create(
            {
                "store_id": cls.store.id,
                "product_id": cls.product.id,
                "shopify_product_id": "88001",
                "shopify_variant_id": "8801",
            }
        )
        cls.svc = OrderImportService(cls.env)

    def _payload(self, oid, financial_status="pending", total=100.0, qty=1, transactions=None):
        return {
            "id": oid,
            "name": "#%s" % oid,
            "customer": {
                "id": 8801,
                "email": "wpab@example.com",
                "first_name": "W",
                "last_name": "PAB",
            },
            "financial_status": financial_status,
            "payment_gateway_names": ["Paymob"],
            "taxes_included": False,
            "total_price": "%.2f" % total,
            "total_tax": "0.00",
            "total_discounts": "0.00",
            "line_items": [
                {
                    "id": int(oid) if str(oid).isdigit() else 1,
                    "variant_id": 8801,
                    "sku": "WPAB-SKU",
                    "name": "WPAB Widget",
                    "price": "100.00",
                    "quantity": qty,
                    "total_discount": "0.00",
                    "taxable": False,
                    "tax_lines": [],
                }
            ],
            "shipping_lines": [],
            "transactions": transactions or [],
        }

    def _set_on_hand(self, qty):
        Quant = self.env["stock.quant"]
        wh = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)], limit=1
        )
        location = wh.lot_stock_id
        quant = Quant.search(
            [("product_id", "=", self.product.id), ("location_id", "=", location.id)],
            limit=1,
        )
        if quant:
            quant.inventory_quantity = qty
            quant.action_apply_inventory()
        else:
            Quant.with_context(inventory_mode=True).create(
                {
                    "product_id": self.product.id,
                    "location_id": location.id,
                    "inventory_quantity": qty,
                }
            ).action_apply_inventory()

    def test_01_pending_to_paid_registers_payment_on_update(self):
        """SO-03 / WP-A: pending import then paid update registers payment."""
        pending = self._payload("wpab101", financial_status="pending", total=100.0)
        ok, err = self.svc.import_shopify_order(self.store, pending)
        self.assertTrue(ok, err)
        order = self.env["sale.order"].search([("shopify_order_id", "=", "wpab101")], limit=1)
        self.assertTrue(order)
        self.assertFalse(
            order.invoice_ids.filtered(lambda m: m.state == "posted"),
            "pending must not post invoice",
        )

        paid = self._payload(
            "wpab101",
            financial_status="paid",
            total=100.0,
            transactions=[
                {"id": "tx-wpab-101", "kind": "sale", "status": "success", "amount": "100.00"}
            ],
        )
        self.svc.sync_financials_on_update(order, self.store, paid)
        order.invalidate_recordset()
        posted = order.invoice_ids.filtered(lambda m: m.state == "posted")
        self.assertTrue(posted, "paid update must post invoice")
        payments = self.env["account.payment"].search(
            [("shopify_transaction_id", "=", "tx-wpab-101")]
        )
        self.assertEqual(len(payments), 1)

    def test_02_second_partial_payment_not_blocked_by_first_txn(self):
        """SO-04 / WP-A: second installment registers despite first txn still in payload."""
        first = self._payload(
            "wpab102",
            financial_status="partially_paid",
            total=100.0,
            transactions=[
                {"id": "tx-wpab-102a", "kind": "sale", "status": "success", "amount": "40.00"}
            ],
        )
        ok, err = self.svc.import_shopify_order(self.store, first)
        self.assertTrue(ok, err)
        order = self.env["sale.order"].search([("shopify_order_id", "=", "wpab102")], limit=1)
        self.assertAlmostEqual(order.shopify_amount_paid or 0.0, 40.0, places=2)

        second = self._payload(
            "wpab102",
            financial_status="paid",
            total=100.0,
            transactions=[
                {"id": "tx-wpab-102a", "kind": "sale", "status": "success", "amount": "40.00"},
                {"id": "tx-wpab-102b", "kind": "sale", "status": "success", "amount": "60.00"},
            ],
        )
        # extract_paid_amount typically sums successful sale txns
        self.svc.sync_financials_on_update(order, self.store, second)
        order.invalidate_recordset()
        pay_b = self.env["account.payment"].search(
            [("shopify_transaction_id", "=", "tx-wpab-102b")]
        )
        self.assertEqual(len(pay_b), 1, "second installment must register")
        self.assertAlmostEqual(order.shopify_amount_paid or 0.0, 100.0, places=2)

    def test_03_stock_gate_blocks_confirm(self):
        """BC-02 / WP-B: confirm_require_stock keeps SO draft when qty free is 0."""
        self.store.confirm_require_stock = True
        self._set_on_hand(0.0)
        payload = self._payload(
            "wpab103",
            financial_status="paid",
            total=100.0,
            qty=1,
            transactions=[
                {"id": "tx-wpab-103", "kind": "sale", "status": "success", "amount": "100.00"}
            ],
        )
        ok, err = self.svc.import_shopify_order(self.store, payload)
        self.assertTrue(ok, err)
        order = self.env["sale.order"].search([("shopify_order_id", "=", "wpab103")], limit=1)
        self.assertIn(order.state, ("draft", "sent"), "stock gate must block confirm")
        self.assertFalse(order.invoice_ids.filtered(lambda m: m.state == "posted"))

    def test_04_stock_gate_allows_confirm_when_in_stock(self):
        self.store.confirm_require_stock = True
        self._set_on_hand(5.0)
        payload = self._payload(
            "wpab104",
            financial_status="paid",
            total=100.0,
            qty=1,
            transactions=[
                {"id": "tx-wpab-104", "kind": "sale", "status": "success", "amount": "100.00"}
            ],
        )
        ok, err = self.svc.import_shopify_order(self.store, payload)
        self.assertTrue(ok, err)
        order = self.env["sale.order"].search([("shopify_order_id", "=", "wpab104")], limit=1)
        self.assertEqual(order.state, "sale")

    def test_05_queue_update_calls_financial_sync(self):
        """UPDATE queue path must invoke sync_financials_on_update."""
        pending = self._payload("wpab105", financial_status="pending", total=100.0)
        ok, err = self.svc.import_shopify_order(self.store, pending)
        self.assertTrue(ok, err)
        order = self.env["sale.order"].search([("shopify_order_id", "=", "wpab105")], limit=1)

        paid = self._payload(
            "wpab105",
            financial_status="paid",
            total=100.0,
            transactions=[
                {"id": "tx-wpab-105", "kind": "sale", "status": "success", "amount": "100.00"}
            ],
        )
        Queue = self.env["shopify.order.queue"]
        import_service = OrderImportService(self.env)
        order_service = OrderService(self.env, import_service=import_service)
        update_service = OrderUpdateService(self.env, import_service=import_service)
        fulfillment_service = ShopifyFulfillmentService(self.env)
        queue = Queue.create(
            {
                "store_id": self.store.id,
                "shopify_order_id": "wpab105",
                "job_type": "update",
                "state": "pending",
                "payload": "{}",
            }
        )
        trace = llog.LifecycleTrace(
            correlation_id="wpab-test",
            op=llog.OP_UPDATE,
            shop_order="wpab105",
            queue=queue.id,
        )
        ok2, msg = Queue._dispatch_queue(
            queue,
            paid,
            trace,
            import_service,
            order_service,
            update_service,
            fulfillment_service,
        )
        self.assertTrue(ok2, msg)
        order.invalidate_recordset()
        self.assertTrue(order.invoice_ids.filtered(lambda m: m.state == "posted"))
        self.assertEqual(
            len(self.env["account.payment"].search([("shopify_transaction_id", "=", "tx-wpab-105")])),
            1,
        )
