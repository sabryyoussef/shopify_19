"""WP-C/D/E/F/G/H/J — focused tests for the P1/P2 development plan items.

  * WP-C: return picking on cancel/refund after a DONE outgoing delivery.
  * WP-D: shipping address sync (partner_shipping_id + open picking dest) on update.
  * WP-E: inbound Shopify tracking numbers written to stock.picking.
  * WP-F: damaged replacement (exchange) without restock.
  * WP-G: order note + multi-currency mapping on import.
  * WP-H: Shopify order tags stored on the sale order.
  * WP-J: shipping line reconciliation + payment journal remap on update.

All tests run transactionally; nothing is written to live Shopify.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.fulfillment_service import ShopifyFulfillmentService
from ..services.order_service import OrderService
from ..services.order_update_service import OrderUpdateService
from ..services.refund_sync_service import RefundSyncService


@tagged("post_install", "-at_install", "shopify_wp_p1p2")
class TestWpCReturnOnCancelAfterShip(TransactionCase):
    def setUp(self):
        super().setUp()
        self.warehouse = self.env["stock.warehouse"].search([], limit=1)
        self.stock_location = self.warehouse.lot_stock_id
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "WPC Store",
                "shop_url": "https://wpc.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "refund_restock_mode": "odoo_restock",
                "default_return_warehouse_id": self.warehouse.id,
            }
        )
        self.partner = self.env["res.partner"].create({"name": "WPC Customer"})
        self.product = self.env["product.product"].create(
            {
                "name": "WPC Widget",
                "type": "consu",
                "is_storable": True,
                "list_price": 50.0,
                "default_code": "WPC-SKU",
            }
        )
        self.product.shopify_variant_id = "70001"
        self.env["stock.quant"]._update_available_quantity(
            self.product, self.stock_location, 10.0
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": "wpc-order-1",
                "shopify_instance_id": self.store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.product.id,
                "product_uom_qty": 2,
                "price_unit": 50.0,
                "shopify_line_item_id": "wpc-line-1",
            }
        )
        self.order.action_confirm()

        fs = ShopifyFulfillmentService(self.env)
        res = fs.handle_fulfillment(
            self.order,
            self.store,
            {
                "id": "wpc-order-1",
                "fulfillment_status": "fulfilled",
                "fulfillments": [
                    {
                        "id": 9001,
                        "location_id": 1,
                        "line_items": [
                            {"variant_id": "70001", "sku": "WPC-SKU", "quantity": 2}
                        ],
                    }
                ],
            },
        )
        self.assertTrue(res["delivery_completed"], "fixture setup must fully deliver")
        self.refund_service = RefundSyncService(self.env)

    def _returns(self):
        return self.env["stock.picking"].search(
            [
                ("sale_id", "=", self.order.id),
                ("picking_type_id.code", "=", "incoming"),
            ]
        )

    def test_cancel_after_ship_creates_return_picking(self):
        self.refund_service.cancel_order_from_shopify(self.store, self.order, "customer")
        returns = self._returns()
        self.assertTrue(returns, "delivered goods must come back via a return picking")
        self.assertEqual(sum(returns.mapped("move_ids.product_uom_qty")), 2.0)
        self.assertEqual(self.order.state, "cancel")

    def test_cancel_after_ship_credit_note_only_skips_return(self):
        self.store.refund_restock_mode = "credit_note_only"
        self.refund_service.cancel_order_from_shopify(self.store, self.order, "customer")
        self.assertFalse(self._returns(), "credit_note_only must never create a return picking")

    def test_cancel_after_ship_return_is_idempotent(self):
        self.refund_service.cancel_order_from_shopify(self.store, self.order, "customer")
        first = self._returns()
        self.refund_service.cancel_order_from_shopify(self.store, self.order, "customer")
        second = self._returns()
        self.assertEqual(first.ids, second.ids, "duplicate cancel must not create a 2nd return")


@tagged("post_install", "-at_install", "shopify_wp_p1p2")
class TestWpDShippingAddressSync(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "WPD Store",
                "shop_url": "https://wpd.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "order_edit_sync_mode": "draft_sent",
            }
        )
        self.partner = self.env["res.partner"].create({"name": "WPD Customer"})
        self.product = self.env["product.product"].create(
            {"name": "WPD Widget", "type": "consu", "list_price": 50.0, "default_code": "WPD-1"}
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": "wpd-order-1",
                "shopify_instance_id": self.store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 50.0,
                "shopify_line_item_id": "wpd-line-1",
            }
        )
        self.service = OrderUpdateService(self.env)

    def _payload(self, shipping_address):
        return {
            "id": "wpd-order-1",
            "line_items": [
                {
                    "id": "wpd-line-1",
                    "sku": "WPD-1",
                    "name": "WPD Widget",
                    "price": "50.00",
                    "quantity": 1,
                }
            ],
            "shipping_address": shipping_address,
        }

    def test_shipping_address_synced_to_partner_shipping_id(self):
        payload = self._payload(
            {
                "address1": "123 New Street",
                "city": "Cairo",
                "zip": "12345",
                "country_code": "EG",
            }
        )
        self.service.update_order_from_payload(self.order, payload, self.store)
        self.assertTrue(self.order.partner_shipping_id)
        self.assertNotEqual(self.order.partner_shipping_id.id, self.order.partner_id.id)
        self.assertEqual(self.order.partner_shipping_id.type, "delivery")
        self.assertEqual(self.order.partner_shipping_id.street, "123 New Street")
        self.assertEqual(self.order.partner_shipping_id.city, "Cairo")

    def test_shipping_address_updates_open_picking_destination(self):
        self.order.action_confirm()
        picking = self.order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        self.assertTrue(picking)

        payload = self._payload(
            {"address1": "456 Delivery Rd", "city": "Giza", "zip": "54321", "country_code": "EG"}
        )
        self.service.update_order_from_payload(self.order, payload, self.store)
        picking.invalidate_recordset()
        self.assertEqual(picking.partner_id.id, self.order.partner_shipping_id.id)


@tagged("post_install", "-at_install", "shopify_wp_p1p2")
class TestWpEInboundTracking(TransactionCase):
    def setUp(self):
        super().setUp()
        self.warehouse = self.env["stock.warehouse"].search([], limit=1)
        self.stock_location = self.warehouse.lot_stock_id
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "WPE Store",
                "shop_url": "https://wpe.myshopify.com",
                "access_token": "dummy",
                "active": True,
            }
        )
        self.partner = self.env["res.partner"].create({"name": "WPE Customer"})
        self.product = self.env["product.product"].create(
            {
                "name": "WPE Widget",
                "type": "consu",
                "is_storable": True,
                "list_price": 50.0,
                "default_code": "WPE-SKU",
            }
        )
        self.product.shopify_variant_id = "70002"
        self.env["stock.quant"]._update_available_quantity(
            self.product, self.stock_location, 10.0
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": "wpe-order-1",
                "shopify_instance_id": self.store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 50.0,
                "shopify_line_item_id": "wpe-line-1",
            }
        )
        self.order.action_confirm()
        self.fs = ShopifyFulfillmentService(self.env)

    def test_tracking_number_written_to_picking(self):
        self.fs.handle_fulfillment(
            self.order,
            self.store,
            {
                "id": "wpe-order-1",
                "fulfillment_status": "fulfilled",
                "fulfillments": [
                    {
                        "id": 9101,
                        "location_id": 1,
                        "tracking_number": "TRK-123",
                        "line_items": [
                            {"variant_id": "70002", "sku": "WPE-SKU", "quantity": 1}
                        ],
                    }
                ],
            },
        )
        picking = self.order.picking_ids.filtered(lambda p: p.state == "done")
        self.assertTrue(picking)
        self.assertEqual(picking.carrier_tracking_ref, "TRK-123")

    def test_multiple_tracking_numbers_joined_on_picking(self):
        self.fs.handle_fulfillment(
            self.order,
            self.store,
            {
                "id": "wpe-order-1",
                "fulfillment_status": "fulfilled",
                "fulfillments": [
                    {
                        "id": 9102,
                        "location_id": 1,
                        "tracking_numbers": ["TRK-A", "TRK-B"],
                        "line_items": [
                            {"variant_id": "70002", "sku": "WPE-SKU", "quantity": 1}
                        ],
                    }
                ],
            },
        )
        picking = self.order.picking_ids.filtered(lambda p: p.state == "done")
        self.assertEqual(picking.carrier_tracking_ref, "TRK-A,TRK-B")

    def test_existing_tracking_ref_is_not_overwritten(self):
        picking = self.order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        picking.carrier_tracking_ref = "MANUAL-REF"
        self.fs.handle_fulfillment(
            self.order,
            self.store,
            {
                "id": "wpe-order-1",
                "fulfillment_status": "fulfilled",
                "fulfillments": [
                    {
                        "id": 9103,
                        "location_id": 1,
                        "tracking_number": "TRK-NEW",
                        "line_items": [
                            {"variant_id": "70002", "sku": "WPE-SKU", "quantity": 1}
                        ],
                    }
                ],
            },
        )
        picking.invalidate_recordset()
        self.assertEqual(picking.carrier_tracking_ref, "MANUAL-REF")


@tagged("post_install", "-at_install", "shopify_wp_p1p2")
class TestWpFDamagedReplacementNoRestock(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "WPF Store",
                "shop_url": "https://wpf.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "refund_restock_mode": "odoo_restock",
            }
        )
        self.partner = self.env["res.partner"].create({"name": "WPF Customer"})
        self.product = self.env["product.product"].create(
            {"name": "WPF Widget", "type": "consu", "list_price": 100.0, "default_code": "WPF-1"}
        )

    def _order(self, name):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": name,
                "shopify_instance_id": self.store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 100.0,
                "shopify_line_item_id": "L-%s" % name,
            }
        )
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        return order

    def _wizard(self, order, restock_mode):
        return self.env["shopify.exchange.wizard"].create(
            {
                "sale_order_id": order.id,
                "store_id": self.store.id,
                "replacement_restock_mode": restock_mode,
                "note": "Damaged item exchange",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sale_line_id": order.order_line[0].id,
                            "return_qty": 1,
                            "replacement_product_id": self.product.id,
                            "replacement_qty": 1,
                            "replacement_price_unit": 100.0,
                        },
                    )
                ],
            }
        )

    def test_store_default_replacement_restock_mode(self):
        self.assertEqual(self.store.replacement_restock_mode, "restock")

    def test_no_restock_skips_return_picking(self):
        order = self._order("wpf-damaged")
        wiz = self._wizard(order, "no_restock")
        wiz.action_process_exchange()
        returns = self.env["stock.picking"].search(
            [("sale_id", "=", order.id), ("picking_type_id.code", "=", "incoming")]
        )
        self.assertFalse(returns, "damaged/no_restock exchange must not restock")
        credit = self.env["account.move"].search(
            [("shopify_refund_id", "=", "exchange-%s" % order.id)], limit=1
        )
        self.assertTrue(credit, "credit note must still be created")

    def test_restock_mode_creates_return_picking(self):
        order = self._order("wpf-normal")
        wiz = self._wizard(order, "restock")
        wiz.action_process_exchange()
        returns = self.env["stock.picking"].search(
            [("sale_id", "=", order.id), ("picking_type_id.code", "=", "incoming")]
        )
        self.assertTrue(returns, "normal exchange must restock per store config")


@tagged("post_install", "-at_install", "shopify_wp_p1p2")
class TestWpGNoteCurrencyAndWpHTags(TransactionCase):
    def setUp(self):
        super().setUp()
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "WPGH Store",
                "shop_url": "https://wpgh.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "auto_create_product_if_not_found": True,
            }
        )
        self.currency_eur = self.env.ref("base.EUR")
        self.currency_eur.sudo().write({"active": True})
        self.order_service = OrderService(self.env)

    def _payload(self, oid, note="", currency=None, tags=None):
        payload = {
            "id": oid,
            "name": "#%s" % oid,
            "customer": {"id": 501, "email": "wpgh@example.com", "first_name": "W", "last_name": "GH"},
            "financial_status": "pending",
            "line_items": [
                {
                    "id": 1,
                    "variant_id": 501,
                    "sku": "WPGH-SKU",
                    "name": "WPGH Widget",
                    "price": "10.00",
                    "quantity": 1,
                }
            ],
            "shipping_lines": [],
        }
        if note:
            payload["note"] = note
        if currency:
            payload["currency"] = currency
        if tags is not None:
            payload["tags"] = tags
        return payload

    def test_note_and_currency_and_tags_synced_on_create(self):
        payload = self._payload(
            "wpgh-1", note="Please gift wrap", currency="EUR", tags="VIP, Wholesale"
        )
        order = self.order_service.create_order_from_payload(payload, store=self.store)
        self.assertIn("Please gift wrap", order.note or "")
        self.assertEqual(order.currency_id.id, self.currency_eur.id)
        self.assertEqual(order.shopify_tags, "VIP, Wholesale")

    def test_unknown_currency_is_skipped(self):
        payload = self._payload("wpgh-2", currency="ZZZ")
        order = self.order_service.create_order_from_payload(payload, store=self.store)
        self.assertNotEqual(order.currency_id.name, "ZZZ")

    def test_tags_updated_on_order_edit(self):
        create_payload = self._payload("wpgh-3", tags="Initial")
        order = self.order_service.create_order_from_payload(create_payload, store=self.store)
        self.assertEqual(order.shopify_tags, "Initial")

        update_service = OrderUpdateService(self.env)
        update_payload = dict(create_payload)
        update_payload["tags"] = "Updated, Priority"
        update_service.update_order_from_payload(order, update_payload, self.store)
        self.assertEqual(order.shopify_tags, "Updated, Priority")

    def test_currency_not_changed_once_invoice_posted(self):
        create_payload = self._payload("wpgh-4")
        order = self.order_service.create_order_from_payload(create_payload, store=self.store)
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        currency_before = order.currency_id.id

        update_service = OrderUpdateService(self.env)
        update_payload = dict(create_payload)
        update_payload["currency"] = "EUR"
        update_service.update_order_from_payload(order, update_payload, self.store)
        self.assertEqual(order.currency_id.id, currency_before)


@tagged("post_install", "-at_install", "shopify_wp_p1p2")
class TestWpJShippingAndPaymentMethodUpdate(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.delivery_product = self.env["product.product"].create(
            {"name": "WPJ Shipping", "type": "service", "default_code": "WPJ-SHIP"}
        )
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "WPJ Store",
                "shop_url": "https://wpj.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "delivery_product_id": self.delivery_product.id,
                "order_edit_sync_mode": "draft_sent",
            }
        )
        self.partner = self.env["res.partner"].create({"name": "WPJ Customer"})
        self.product = self.env["product.product"].create(
            {"name": "WPJ Widget", "type": "consu", "list_price": 100.0, "default_code": "WPJ-1"}
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shopify_order_id": "wpj-order-1",
                "shopify_instance_id": self.store.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 100.0,
                "shopify_line_item_id": "wpj-line-1",
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": self.delivery_product.id,
                "name": "Shipping",
                "product_uom_qty": 1,
                "price_unit": 10.0,
            }
        )
        self.service = OrderUpdateService(self.env)

    def _ship_line(self, order=None):
        order = order or self.order
        return order.order_line.filtered(lambda l: l.product_id.id == self.delivery_product.id)

    def _base_payload(self, shipping_lines=None):
        return {
            "id": "wpj-order-1",
            "line_items": [
                {
                    "id": "wpj-line-1",
                    "sku": "WPJ-1",
                    "name": "WPJ Widget",
                    "price": "100.00",
                    "quantity": 1,
                }
            ],
            "shipping_lines": shipping_lines if shipping_lines is not None else [],
        }

    def test_shipping_line_price_updated_on_edit(self):
        payload = self._base_payload(
            shipping_lines=[{"title": "Express", "price": "25.00"}]
        )
        self.service.update_order_from_payload(self.order, payload, self.store)
        line = self._ship_line()
        self.assertEqual(line.price_unit, 25.0)
        self.assertEqual(line.name, "Express")
        self.assertEqual(line.product_uom_qty, 1.0)

    def test_shipping_line_removed_when_is_removed(self):
        payload = self._base_payload(
            shipping_lines=[{"title": "Express", "price": "25.00", "is_removed": True}]
        )
        self.service.update_order_from_payload(self.order, payload, self.store)
        line = self._ship_line()
        self.assertEqual(line.product_uom_qty, 0.0)

    def _two_sale_journals(self):
        """Two 'sale'-type journals: the only type Odoo allows on an
        out_invoice's own journal_id, and thus the only valid target for the
        WP-J invoice-journal remap (see _remap_payment_journal)."""
        journal_a = self.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", self.company.id)], limit=1
        )
        journal_b = self.env["account.journal"].create(
            {
                "name": "WPJ Sales B",
                "type": "sale",
                "code": "WPJSLB",
                "company_id": self.company.id,
            }
        )
        return journal_a, journal_b

    def _bank_journal(self):
        return self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)], limit=1
        )

    def test_payment_gateway_journal_remap_when_unpaid(self):
        journal_a, journal_b = self._two_sale_journals()
        self.assertTrue(journal_a and journal_b, "test requires two distinct sale journals")

        Gw = self.env["shopify.payment.gateway"]
        Gw.create(
            {
                "name": "Gateway A",
                "instance_id": self.store.id,
                "payment_code": "GatewayA",
                "odoo_journal_id": journal_a.id,
            }
        )
        gw_b = Gw.create(
            {
                "name": "Gateway B",
                "instance_id": self.store.id,
                "payment_code": "GatewayB",
                "odoo_journal_id": journal_b.id,
            }
        )

        self.order.action_confirm()
        invoice = self.order._create_invoices()
        invoice.journal_id = journal_a.id
        self.assertEqual(invoice.state, "draft")

        payload = self._base_payload()
        payload["payment_gateway_names"] = ["GatewayB"]
        self.service.update_order_from_payload(self.order, payload, self.store)

        invoice.invalidate_recordset()
        self.assertEqual(invoice.journal_id.id, gw_b.odoo_journal_id.id)

    def test_payment_gateway_journal_not_remapped_once_paid(self):
        journal_a, journal_b = self._two_sale_journals()
        bank_journal = self._bank_journal()
        self.assertTrue(
            journal_a and journal_b and bank_journal,
            "test requires two sale journals and a bank journal",
        )
        income = self.env["account.account"].search(
            [("account_type", "=", "income")], limit=1
        )
        if income:
            self.product.property_account_income_id = income

        Gw = self.env["shopify.payment.gateway"]
        Gw.create(
            {
                "name": "Gateway A",
                "instance_id": self.store.id,
                "payment_code": "GatewayA",
                "odoo_journal_id": journal_a.id,
            }
        )
        Gw.create(
            {
                "name": "Gateway B",
                "instance_id": self.store.id,
                "payment_code": "GatewayB",
                "odoo_journal_id": journal_b.id,
            }
        )

        self.order.action_confirm()
        invoice = self.order._create_invoices()
        invoice.journal_id = journal_a.id
        invoice.action_post()

        register_wizard = (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create({"journal_id": bank_journal.id})
        )
        register_wizard._create_payments()
        invoice.invalidate_recordset()
        self.assertEqual(invoice.payment_state, "paid")

        payload = self._base_payload()
        payload["payment_gateway_names"] = ["GatewayB"]
        self.service.update_order_from_payload(self.order, payload, self.store)

        invoice.invalidate_recordset()
        self.assertEqual(
            invoice.journal_id.id, journal_a.id, "journal must never remap once paid"
        )
