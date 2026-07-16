"""P8 — Structured lifecycle logging for the Shopify order sync.

Emits greppable, single-line INFO logs so a single order can be traced
end-to-end across webhook -> queue -> sale order -> picking -> invoice ->
payment. All lines share the prefix ``SHOPIFY_SYNC`` and a stable
``corr=<correlation id>`` so operators can ``grep`` one order's full journey.

Design rules:
- Never log secrets, access tokens or webhook secrets. Only identifiers,
  names, statuses and short human messages are emitted here.
- Callers pass identifiers (Shopify order id, Odoo SO, queue id, ...); this
  module only formats/sanitizes them.
"""

import logging
import re
import uuid

_logger = logging.getLogger("shopify.lifecycle")

LOG_PREFIX = "SHOPIFY_SYNC"

# ---------------------------------------------------------------------------
# Lifecycle steps (stable, greppable tokens)
# ---------------------------------------------------------------------------
STEP_EVENT_RECEIVED = "EventReceived"
STEP_QUEUE_CREATED = "QueueCreated"
STEP_QUEUE_PROCESSING = "QueueProcessing"
STEP_EXISTING_ORDER_FOUND = "ExistingOrderFound"
STEP_UPDATE_STRATEGY_SELECTED = "UpdateStrategySelected"
STEP_SO_CREATED = "SOCreated"
STEP_SO_UPDATED = "SOUpdated"
STEP_PICKING_UPDATED = "PickingUpdated"
# P4 — fulfillment lifecycle
STEP_FULFILLMENT_RECEIVED = "FulfillmentReceived"
STEP_FULFILLMENT_MAPPED = "FulfillmentMapped"
STEP_FULFILLMENT_DUPLICATE = "FulfillmentDuplicate"
STEP_FULFILLMENT_APPLIED = "FulfillmentApplied"
STEP_PARTIAL_FULFILLMENT_APPLIED = "PartialFulfillmentApplied"
STEP_PICKING_VALIDATED = "PickingValidated"
STEP_FULFILLMENT_MAPPING_FAILED = "FulfillmentMappingFailed"
STEP_FULFILLMENT_REVERSAL_DETECTED = "FulfillmentReversalDetected"
STEP_FULFILLMENT_PUSH_SKIPPED = "FulfillmentPushSkipped"
STEP_INVOICE_DECISION = "InvoiceDecision"
STEP_PAYMENT_DECISION = "PaymentDecision"
STEP_PAYMENT_REGISTERED = "PaymentRegistered"
STEP_PAYMENT_SKIPPED = "PaymentSkipped"
STEP_GATEWAY_UNMAPPED = "GatewayUnmapped"
STEP_TOTAL_RECONCILIATION = "TotalReconciliation"
STEP_TAX_MAPPING = "TaxMapping"
STEP_SHIPPING_MAPPING = "ShippingMapping"
STEP_COMPLETED = "Completed"
STEP_FAILURE = "Failure"
STEP_RETRY_SCHEDULED = "RetryScheduled"
STEP_SKIPPED = "Skipped"

# ---------------------------------------------------------------------------
# Operation types (lifecycle events must be distinguished, not generic import)
# ---------------------------------------------------------------------------
OP_CREATE = "create"
OP_UPDATE = "update"
OP_FULFILLMENT = "fulfillment"
OP_PAYMENT = "payment"
OP_REFUND = "refund"
OP_CANCELLATION = "cancellation"
OP_ORDER = "order"  # legacy / auto (create-or-update decided at processing time)

VALID_OPERATIONS = frozenset(
    {OP_CREATE, OP_UPDATE, OP_FULFILLMENT, OP_PAYMENT, OP_REFUND, OP_CANCELLATION, OP_ORDER}
)

# Map Shopify webhook topics -> lifecycle operation.
_TOPIC_OPERATION = {
    "orders/create": OP_CREATE,
    "orders/updated": OP_UPDATE,
    "orders/edited": OP_UPDATE,
    "orders/cancelled": OP_CANCELLATION,
    "orders/delete": OP_CANCELLATION,
    "refunds/create": OP_REFUND,
    "fulfillments/create": OP_FULFILLMENT,
    "fulfillments/update": OP_FULFILLMENT,
    "orders/fulfilled": OP_FULFILLMENT,
    "orders/partially_fulfilled": OP_FULFILLMENT,
    "orders/paid": OP_PAYMENT,
    "transactions/create": OP_PAYMENT,
}

_CTRL_RE = re.compile(r"[\r\n\t]+")


def operation_from_topic(topic):
    """Return the lifecycle operation for a Shopify webhook topic.

    Unknown/absent topics fall back to the legacy/auto ``order`` operation so
    the processor decides create-vs-update at runtime.
    """
    key = (topic or "").strip().lower()
    if key in _TOPIC_OPERATION:
        return _TOPIC_OPERATION[key]
    if key.startswith("orders/"):
        return OP_ORDER
    return OP_ORDER


def new_correlation_id():
    """Return a short, unique correlation id (no PII/secrets)."""
    return "sh-%s" % uuid.uuid4().hex[:16]


def _clean(value):
    if value is None or value is False or value == "":
        return None
    text = _CTRL_RE.sub(" ", str(value)).strip()
    return text or None


class LifecycleTrace:
    """Carries correlation context and emits structured lifecycle log lines.

    Usage::

        trace = LifecycleTrace(op="update", shop_order="123", correlation_id=corr)
        trace.step(STEP_QUEUE_PROCESSING, queue=queue.id)
        trace.step(STEP_SO_UPDATED, so=order.name, status="ok", msg="in_place")
    """

    __slots__ = (
        "correlation_id",
        "op",
        "shop_order",
        "shop_name",
        "so",
        "queue",
        "webhook",
        "txn",
        "fulfillment",
        "refund",
    )

    def __init__(
        self,
        correlation_id=None,
        op=None,
        shop_order=None,
        shop_name=None,
        so=None,
        queue=None,
        webhook=None,
        txn=None,
        fulfillment=None,
        refund=None,
    ):
        self.correlation_id = correlation_id or new_correlation_id()
        self.op = op
        self.shop_order = shop_order
        self.shop_name = shop_name
        self.so = so
        self.queue = queue
        self.webhook = webhook
        self.txn = txn
        self.fulfillment = fulfillment
        self.refund = refund

    def bind(self, **kwargs):
        """Attach/override context identifiers for subsequent steps."""
        for key, value in kwargs.items():
            if value in (None, False, ""):
                continue
            if key in self.__slots__:
                setattr(self, key, value)
        return self

    def step(self, step, status="ok", msg=None, level=logging.INFO, **overrides):
        """Emit one structured lifecycle line.

        ``overrides`` may set any context field for this line only
        (e.g. ``so=order.name`` or ``queue=queue.id``).
        """
        fields = [
            ("step", step),
            ("op", overrides.get("op", self.op)),
            ("corr", self.correlation_id),
            ("shop_order", overrides.get("shop_order", self.shop_order)),
            ("shop_name", overrides.get("shop_name", self.shop_name)),
            ("so", overrides.get("so", self.so)),
            ("queue", overrides.get("queue", self.queue)),
            ("webhook", overrides.get("webhook", self.webhook)),
            ("txn", overrides.get("txn", self.txn)),
            ("fulfillment", overrides.get("fulfillment", self.fulfillment)),
            ("refund", overrides.get("refund", self.refund)),
            ("status", status),
        ]
        parts = ["%s=%s" % (key, _clean(val)) for key, val in fields if _clean(val) is not None]
        line = "%s %s" % (LOG_PREFIX, " ".join(parts))
        cleaned_msg = _clean(msg)
        if cleaned_msg is not None:
            # Bound message length to keep logs readable; never contains secrets.
            if len(cleaned_msg) > 500:
                cleaned_msg = cleaned_msg[:497] + "..."
            line = '%s msg="%s"' % (line, cleaned_msg.replace('"', "'"))
        _logger.log(level, line)
        return line
