"""P8 — Structured lifecycle logging tests."""
import json
import logging

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services import lifecycle_logger as llog


class _CaptureHandler(logging.Handler):
    """Collects formatted log lines emitted on a logger."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@tagged("post_install", "-at_install", "shopify_p8")
class TestP8LifecycleLogging(TransactionCase):
    def setUp(self):
        super().setUp()
        self.noop_workflow = self.env["shopify.sale.auto.workflow"].sudo().create(
            {
                "name": "P8 Noop Workflow",
                "confirm_quotation": False,
                "create_invoice": False,
                "validate_invoice": False,
                "register_payment": False,
            }
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "shopify.auto_heal_workflow", "False"
        )
        self.store = self.env["shopify.store"].sudo().create(
            {
                "name": "P8 Store",
                "shop_url": "https://p8-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "manage_orders_webhook": True,
                "auto_create_product_if_not_found": True,
                "sale_auto_workflow_id": self.noop_workflow.id,
            }
        )

    def _payload(self, oid="p8-1", name="#P8-1", qty=1, price=100.0):
        return {
            "id": oid,
            "name": name,
            "customer": {"id": 8801, "email": "p8@example.com", "first_name": "P", "last_name": "8"},
            "line_items": [
                {
                    "id": "L-%s" % oid,
                    "variant_id": 88011,
                    "sku": "P8-SKU",
                    "name": "P8 Widget",
                    "price": str(price),
                    "quantity": qty,
                }
            ],
            "total_price": str(price * qty),
        }

    def test_01_operation_from_topic(self):
        self.assertEqual(llog.operation_from_topic("orders/create"), llog.OP_CREATE)
        self.assertEqual(llog.operation_from_topic("orders/updated"), llog.OP_UPDATE)
        self.assertEqual(llog.operation_from_topic("orders/cancelled"), llog.OP_CANCELLATION)
        self.assertEqual(llog.operation_from_topic("refunds/create"), llog.OP_REFUND)
        self.assertEqual(llog.operation_from_topic("fulfillments/create"), llog.OP_FULFILLMENT)
        self.assertEqual(llog.operation_from_topic("orders/paid"), llog.OP_PAYMENT)
        # Unknown / missing topics fall back to legacy/auto.
        self.assertEqual(llog.operation_from_topic("orders/anything_else"), llog.OP_ORDER)
        self.assertEqual(llog.operation_from_topic(None), llog.OP_ORDER)

    def test_02_correlation_id_unique_and_prefixed(self):
        a = llog.new_correlation_id()
        b = llog.new_correlation_id()
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("sh-"))

    def test_03_trace_line_format_and_sanitization(self):
        trace = llog.LifecycleTrace(
            correlation_id="sh-fmt", op="update", shop_order="123", shop_name="#123"
        )
        line = trace.step(llog.STEP_QUEUE_PROCESSING, queue=7, so="S001", msg="hello world")
        self.assertTrue(line.startswith("SHOPIFY_SYNC "))
        self.assertIn("step=QueueProcessing", line)
        self.assertIn("corr=sh-fmt", line)
        self.assertIn("op=update", line)
        self.assertIn("shop_order=123", line)
        self.assertIn("queue=7", line)
        self.assertIn("so=S001", line)
        self.assertIn('msg="hello world"', line)
        # Control characters must be flattened to keep one greppable line.
        line2 = trace.step("Weird", msg="a\nb\tc\rd")
        self.assertNotIn("\n", line2)
        self.assertNotIn("\t", line2)

    def test_04_create_log_persists_correlation_id(self):
        log = self.env["shopify.sync.log.mixin"].create_log(
            store=self.store,
            log_type="order",
            message="corr test",
            correlation_id="sh-persist",
        )
        self.assertEqual(log.correlation_id, "sh-persist")

    def test_05_end_to_end_greppable_trace(self):
        """One create event must be traceable end-to-end by a single corr id."""
        payload = self._payload(oid="p8-e2e", name="#P8-E2E")
        logger = logging.getLogger("shopify.lifecycle")
        handler = _CaptureHandler()
        prev_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            self.env["shopify.webhook.handler"].sudo().process_webhook_order(
                payload,
                self.store,
                operation=llog.OP_CREATE,
                webhook_id="wh-p8-e2e",
                correlation_id="sh-e2e",
            )
            self.env["shopify.order.queue"].sudo().process_queue()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)
        text = "\n".join(handler.lines)
        self.assertTrue(handler.lines, "lifecycle logger must emit records")
        # All key lifecycle steps present…
        for step in (
            llog.STEP_EVENT_RECEIVED,
            llog.STEP_QUEUE_CREATED,
            llog.STEP_QUEUE_PROCESSING,
            llog.STEP_COMPLETED,
        ):
            self.assertIn("step=%s" % step, text)
        # …and tied to one correlation id.
        self.assertIn("corr=sh-e2e", text)
        self.assertIn("shop_order=p8-e2e", text)
        # The created sale order is discoverable and the correlation id is stored
        # on both the queue and the sync log for DB-level tracing.
        order = self.env["sale.order"].search(
            [("shopify_order_id", "=", "p8-e2e"), ("shopify_instance_id", "=", self.store.id)]
        )
        self.assertEqual(len(order), 1)
        queue = self.env["shopify.order.queue"].search(
            [("shopify_order_id", "=", "p8-e2e"), ("store_id", "=", self.store.id)]
        )
        self.assertEqual(queue.correlation_id, "sh-e2e")
