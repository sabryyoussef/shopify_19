import base64
import hashlib
import hmac
import json
import logging
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import lifecycle_logger as llog


_logger = logging.getLogger(__name__)


class ShopifyWebhookEvent(models.Model):
    _name = "shopify.webhook.event"
    _description = "Shopify Webhook Event"
    _order = "create_date desc"

    store_id = fields.Many2one("shopify.store", required=True, index=True)
    webhook_id = fields.Char(required=True, index=True)
    topic = fields.Char(index=True)
    shop_domain = fields.Char(index=True)
    shopify_order_id = fields.Char(index=True)
    payload = fields.Text(help="Raw webhook payload for replay diagnostics.")
    status = fields.Selection(
        [
            ("received", "Received"),
            ("processing", "Processing"),
            ("processed", "Processed"),
            ("failed", "Failed"),
        ],
        required=True,
        default="received",
        index=True,
    )
    error_message = fields.Text()
    error_type = fields.Char(index=True)
    attempt = fields.Integer(default=0)
    last_attempt_at = fields.Datetime()

    _sql_constraints = [
        (
            "shopify_webhook_event_unique_delivery",
            "unique(store_id, webhook_id)",
            "This Shopify webhook delivery was already received.",
        )
    ]

    def action_replay(self):
        """Reprocess a webhook via the shared handler service layer."""
        for event in self:
            if event.status == "processing":
                raise UserError(
                    _("Webhook %s is already being processed.")
                    % (event.webhook_id or event.id)
                )
            event.env["shopify.webhook.handler"].replay_webhook_event(event)
        return True


class ShopifyWebhookHandler(models.AbstractModel):
    _name = "shopify.webhook.handler"
    _description = "Shopify Webhook Handler"

    @api.model
    def _normalize_shop_domain(self, shop_domain):
        value = (shop_domain or "").strip().lower()
        if not value:
            return ""
        if "://" in value:
            parsed = urlparse(value)
            value = (parsed.netloc or parsed.path or "").strip().lower()
        return value.split("/")[0]

    @api.model
    def get_store_for_webhook(self, shop_domain):
        normalized = self._normalize_shop_domain(shop_domain)
        if not normalized:
            return self.env["shopify.store"]

        stores = self.env["shopify.store"].sudo().search([("active", "=", True)])
        for store in stores:
            store_domain = self._normalize_shop_domain(store.shop_url)
            if store_domain == normalized:
                return store
        return self.env["shopify.store"]

    @api.model
    def _verify_hmac(self, secret, data, hmac_header):
        if not secret or not hmac_header:
            return False
        digest = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).digest()
        calculated = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(calculated, hmac_header)

    @api.model
    def validate_webhook_request(self, store, raw_data, hmac_header, webhook_id):
        """Validate active store webhook HMAC and delivery id presence."""
        if not store:
            return False, "Unknown store"
        if not store.active:
            return False, "Inactive store"
        if not store.webhook_secret:
            return False, "Missing webhook secret for active store"
        if not webhook_id:
            return False, "Missing webhook delivery id"
        if not self._verify_hmac(store.webhook_secret, raw_data, hmac_header):
            return False, "Invalid webhook signature"
        return True, ""

    @api.model
    def _extract_shopify_order_id(self, payload_text_or_dict):
        payload = payload_text_or_dict
        if isinstance(payload_text_or_dict, str):
            try:
                payload = json.loads(payload_text_or_dict or "{}")
            except Exception:
                return False
        if not isinstance(payload, dict):
            return False
        order_id = payload.get("id") or payload.get("order_id")
        return str(order_id) if order_id else False

    @api.model
    def register_webhook_delivery(self, store, webhook_id, topic, shop_domain, raw_payload):
        """Create event history row; return False when already received."""
        if not (store and webhook_id):
            return False

        existing = (
            self.env["shopify.webhook.event"]
            .sudo()
            .search([("store_id", "=", store.id), ("webhook_id", "=", webhook_id)], limit=1)
        )
        if existing:
            return False

        return self.env["shopify.webhook.event"].sudo().create(
            {
                "store_id": store.id,
                "webhook_id": webhook_id,
                "topic": topic or "",
                "shop_domain": self._normalize_shop_domain(shop_domain),
                "payload": raw_payload,
                "shopify_order_id": self._extract_shopify_order_id(raw_payload),
                "status": "received",
                "attempt": 0,
            }
        )

    @api.model
    def _classify_error(self, error_message):
        text = (error_message or "").lower()
        if "timeout" in text:
            return "timeout"
        if "rate" in text and "limit" in text:
            return "rate_limit"
        if "hmac" in text or "signature" in text:
            return "auth"
        if "json" in text or "payload" in text:
            return "validation"
        if error_message:
            return "processing"
        return False

    @api.model
    def mark_webhook_event_status(self, event, status, error_message=None):
        if not event:
            return False
        values = {"status": status}
        if error_message:
            values["error_message"] = error_message
            values["error_type"] = self._classify_error(error_message)
        elif status in ("processed", "received"):
            values["error_message"] = False
            values["error_type"] = False
        return event.sudo().write(values)

    @api.model
    def replay_webhook_event(self, event):
        """
        Re-run a stored webhook delivery through the same processing paths.
        Preserves webhook_id and shopify_order_id; increments attempt count.
        """
        if not event:
            return False
        if event.status == "processing":
            raise UserError(_("Webhook is already being processed."))

        event.sudo().write(
            {
                "status": "processing",
                "attempt": (event.attempt or 0) + 1,
                "last_attempt_at": fields.Datetime.now(),
                "error_message": False,
                "error_type": False,
            }
        )

        store = event.store_id
        topic = (event.topic or "").lower()
        try:
            payload = json.loads(event.payload or "{}")
        except Exception as exc:
            self.mark_webhook_event_status(event, "failed", "Invalid stored JSON: %s" % exc)
            return False

        # Keep order id stable even if payload is partial
        order_id = self._extract_shopify_order_id(payload) or event.shopify_order_id
        if order_id and order_id != event.shopify_order_id:
            event.sudo().write({"shopify_order_id": order_id})

        replay_operation = llog.operation_from_topic(topic)
        replay_corr = "replay-%s" % (event.webhook_id or event.id)
        try:
            # Single source of routing truth: same operation-aware router as live
            # ingestion. Replay is thus idempotent (dedup guards downstream).
            self._route_operation(
                store,
                replay_operation,
                payload,
                event.shop_domain,
                event.webhook_id,
                replay_corr,
            )
            self.mark_webhook_event_status(event, "processed")
            return True
        except Exception as exc:
            _logger.exception("Webhook replay failed for webhook_id=%s", event.webhook_id)
            self.mark_webhook_event_status(event, "failed", str(exc))
            return False

    @api.model
    def ingest_webhook(
        self,
        store,
        raw_data,
        hmac_header,
        webhook_id,
        topic,
        shop_domain,
        correlation_id=None,
    ):
        """Single, operation-aware webhook entry point (P1).

        Order of guarantees: receive -> HMAC (raw body) -> idempotent register ->
        route by operation. Controllers stay thin and both HTTP and tests use
        this method, so no business logic lives in the controllers. Emits P8
        structured steps and never logs the secret.

        Returns a dict: {ok, code, operation, duplicate?, reason?}.
        """
        op = llog.operation_from_topic(topic)
        corr = correlation_id or (("wh-%s" % webhook_id) if webhook_id else llog.new_correlation_id())
        trace = llog.LifecycleTrace(correlation_id=corr, op=op, webhook=webhook_id)
        trace.step(llog.STEP_WEBHOOK_RECEIVED, msg="topic=%s" % (topic or ""))

        valid, reason = self.validate_webhook_request(store, raw_data, hmac_header, webhook_id)
        if not valid:
            trace.step(
                llog.STEP_HMAC_FAILED,
                status="rejected",
                level=logging.WARNING,
                msg=reason,
            )
            code = 400 if "delivery id" in (reason or "") else 403
            return {"ok": False, "code": code, "reason": reason}
        trace.step(llog.STEP_HMAC_VALIDATED, status="ok")

        raw_text = raw_data.decode("utf-8", errors="ignore") if isinstance(raw_data, bytes) else str(raw_data)
        try:
            payload = json.loads(raw_text or "{}")
        except Exception:
            return {"ok": False, "code": 400, "reason": "Invalid JSON"}

        shopify_order_id = str(payload.get("id") or payload.get("order_id") or "")
        trace.bind(shop_order=shopify_order_id or None)

        event = self.register_webhook_delivery(
            store=store,
            webhook_id=webhook_id,
            topic=topic,
            shop_domain=shop_domain,
            raw_payload=raw_text[:100000],
        )
        if webhook_id and not event:
            trace.step(
                llog.STEP_WEBHOOK_DUPLICATE,
                status="idempotent",
                msg="delivery already processed",
            )
            return {"ok": True, "code": 200, "duplicate": True, "operation": op}
        if event:
            trace.step(llog.STEP_EVENT_REGISTERED, msg="event=%s" % event.id)

        try:
            self._route_operation(store, op, payload, shop_domain, webhook_id, corr, trace)
            self.mark_webhook_event_status(event, "processed")
        except Exception as exc:
            self.mark_webhook_event_status(event, "failed", str(exc))
            trace.step(llog.STEP_FAILURE, status="failed", level=logging.ERROR, msg=str(exc))
            raise
        return {"ok": True, "code": 200, "operation": op}

    @api.model
    def _find_store_order(self, store, shopify_order_id):
        if not shopify_order_id:
            return self.env["sale.order"].sudo().browse()
        return self.env["sale.order"].sudo().search(
            [
                ("shopify_order_id", "=", str(shopify_order_id)),
                ("shopify_instance_id", "=", store.id),
            ],
            limit=1,
        )

    @api.model
    def _route_operation(self, store, op, payload, shop_domain, webhook_id, correlation_id, trace=None):
        """Operation-aware router shared by webhook + replay.

        CREATE/UPDATE/FULFILLMENT/ORDER -> queue router (P2/P4).
        CANCELLATION -> safe cancellation service (never deletes posted docs).
        REFUND -> refund service with refund-id idempotency.
        """
        if op in (llog.OP_CREATE, llog.OP_UPDATE, llog.OP_FULFILLMENT, llog.OP_ORDER):
            self.process_webhook_order(
                payload,
                store,
                shop_domain=shop_domain,
                is_valid_hmac=True,
                operation=op,
                webhook_id=webhook_id,
                correlation_id=correlation_id,
            )
            return True

        shopify_order_id = str(payload.get("id") or payload.get("order_id") or "")
        if op == llog.OP_CANCELLATION:
            order = self._find_store_order(store, shopify_order_id)
            if not order or order.state == "cancel" or getattr(order, "shopify_cancelled", False):
                return True
            from ..services.refund_sync_service import RefundSyncService

            RefundSyncService(self.env).cancel_order_from_shopify(
                store, order, payload.get("cancel_reason") or payload.get("reason")
            )
            return True

        if op == llog.OP_REFUND:
            order_id = str(payload.get("order_id") or (payload.get("order") or {}).get("id") or "")
            order = self._find_store_order(store, order_id)
            if not order:
                return True
            refund_id = str(payload.get("id") or "")
            Move = self.env["account.move"].sudo()
            if refund_id and Move.search_count([("shopify_refund_id", "=", refund_id)]):
                return True  # duplicate refund delivery
            from ..services.refund_sync_service import RefundSyncService

            RefundSyncService(self.env).sync_refund_from_webhook(store, order, payload)
            return True

        # Unknown operation: fall back to order queue (create-or-update at runtime).
        self.process_webhook_order(
            payload,
            store,
            shop_domain=shop_domain,
            is_valid_hmac=True,
            operation=llog.OP_ORDER,
            webhook_id=webhook_id,
            correlation_id=correlation_id,
        )
        return True

    @api.model
    def process_webhook_order(
        self,
        payload,
        store,
        shop_domain=None,
        is_valid_hmac=None,
        operation=None,
        webhook_id=None,
        correlation_id=None,
    ):
        """Enqueue a Shopify order event for lifecycle processing.

        Routing rules (P2):
        - New order            -> enqueue CREATE.
        - Existing order + UPDATE (orders/updated) -> enqueue UPDATE
          (must NOT be silently skipped).
        - Existing order + CREATE (orders/create)  -> idempotent, no enqueue
          (avoids duplicate sale orders on webhook replays).
        - Legacy/auto ('order') for an existing order -> enqueue UPDATE.
        """
        try:
            if not store:
                _logger.error("No matching store for domain: %s", shop_domain)
                return False

            if not store.manage_orders_webhook:
                _logger.error("Webhook disabled for store")
                return False

            shopify_order_id = payload.get("id") or payload.get("order_id")
            op = operation or llog.OP_ORDER
            trace = llog.LifecycleTrace(
                correlation_id=correlation_id or llog.new_correlation_id(),
                op=op,
                shop_order=shopify_order_id,
                shop_name=payload.get("name"),
                webhook=webhook_id,
            )
            trace.step(llog.STEP_EVENT_RECEIVED, msg="operation=%s" % op)

            queue_model = self.env["shopify.order.queue"]

            # P4 — fulfillment events route to the dedicated fulfillment handler.
            # No create/update dedup collapse: multiple shipments for one order
            # produce distinct queue rows, each made idempotent downstream by the
            # Shopify fulfillment id.
            if op == llog.OP_FULFILLMENT:
                order_ref = payload.get("order_id") or shopify_order_id
                queue = queue_model.create(
                    {
                        "store_id": store.id,
                        "shopify_order_id": str(order_ref) if order_ref else False,
                        "payload": json.dumps(payload),
                        "state": "pending",
                        "job_type": llog.OP_FULFILLMENT,
                        "correlation_id": trace.correlation_id,
                    }
                )
                trace.step(
                    llog.STEP_QUEUE_CREATED, queue=queue.id, op=llog.OP_FULFILLMENT
                )
                return True

            existing_order = self.env["sale.order"]
            if shopify_order_id:
                existing_order = self.env["sale.order"].search(
                    [
                        ("shopify_order_id", "=", str(shopify_order_id)),
                        ("shopify_instance_id", "=", store.id),
                    ],
                    limit=1,
                )

            if existing_order:
                trace.bind(so=existing_order.name)
                trace.step(
                    llog.STEP_EXISTING_ORDER_FOUND,
                    so=existing_order.name,
                    msg="state=%s" % existing_order.state,
                )
                if op == llog.OP_CREATE:
                    # Duplicate orders/create for an already-imported order.
                    trace.step(
                        llog.STEP_COMPLETED,
                        so=existing_order.name,
                        status="idempotent",
                        msg="duplicate create ignored; order already imported",
                    )
                    return True
                enqueue_op = llog.OP_UPDATE
            else:
                enqueue_op = (
                    llog.OP_CREATE if op in (llog.OP_CREATE, llog.OP_UPDATE) else llog.OP_ORDER
                )

            # Avoid multiple pending/processing queue rows for the same order+op.
            if shopify_order_id:
                existing_queue = queue_model.search(
                    [
                        ("store_id", "=", store.id),
                        ("shopify_order_id", "=", str(shopify_order_id)),
                        ("job_type", "=", enqueue_op),
                        ("state", "in", ["pending", "processing"]),
                    ],
                    limit=1,
                )
                if existing_queue:
                    if not existing_queue.correlation_id:
                        existing_queue.correlation_id = trace.correlation_id
                    trace.step(
                        llog.STEP_QUEUE_CREATED,
                        queue=existing_queue.id,
                        op=enqueue_op,
                        status="dedup",
                        msg="reused existing pending/processing queue",
                    )
                    return True

            queue = queue_model.create(
                {
                    "store_id": store.id,
                    "shopify_order_id": str(shopify_order_id) if shopify_order_id else False,
                    "payload": json.dumps(payload),
                    "state": "pending",
                    "job_type": enqueue_op,
                    "correlation_id": trace.correlation_id,
                }
            )
            trace.step(llog.STEP_QUEUE_CREATED, queue=queue.id, op=enqueue_op)
            return True
        except Exception as e:
            _logger.exception("process_webhook_order failed: %s", str(e))
            raise
