import base64
import hashlib
import hmac
import json
import logging
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError


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

        try:
            if topic.startswith("orders/") or topic in ("", "order", "orders/create", "orders/updated"):
                self.process_webhook_order(
                    payload,
                    store,
                    shop_domain=event.shop_domain,
                    is_valid_hmac=True,
                )
            elif "refund" in topic:
                # Delegate to existing refund webhook path via service (no controller logic)
                order = self.env["sale.order"].sudo().search(
                    [
                        ("shopify_order_id", "=", str(order_id)),
                        ("shopify_instance_id", "=", store.id),
                    ],
                    limit=1,
                )
                from ..services.refund_sync_service import RefundSyncService

                if order:
                    RefundSyncService(self.env).sync_refund_from_webhook(store, order, payload)
            elif "cancel" in topic:
                order = self.env["sale.order"].sudo().search(
                    [
                        ("shopify_order_id", "=", str(order_id)),
                        ("shopify_instance_id", "=", store.id),
                    ],
                    limit=1,
                )
                from ..services.refund_sync_service import RefundSyncService

                if order:
                    RefundSyncService(self.env).cancel_order_from_shopify(
                        store, order, payload.get("cancel_reason")
                    )
            else:
                # Generic: re-queue order processing if order id is present
                self.process_webhook_order(
                    payload,
                    store,
                    shop_domain=event.shop_domain,
                    is_valid_hmac=True,
                )
            self.mark_webhook_event_status(event, "processed")
            return True
        except Exception as exc:
            _logger.exception("Webhook replay failed for webhook_id=%s", event.webhook_id)
            self.mark_webhook_event_status(event, "failed", str(exc))
            return False

    @api.model
    def process_webhook_order(self, payload, store, shop_domain=None, is_valid_hmac=None):
        try:
            _logger.info("Processing Shopify order webhook")

            if not store:
                _logger.error("No matching store for domain: %s", shop_domain)
                return False

            if not store.manage_orders_webhook:
                _logger.error("Webhook disabled for store")
                return False

            shopify_order_id = payload.get("id") or payload.get("order_id")
            queue_model = self.env["shopify.order.queue"]

            # Avoid creating multiple pending/processing queue records
            if shopify_order_id:
                existing = queue_model.search(
                    [
                        ("store_id", "=", store.id),
                        ("shopify_order_id", "=", str(shopify_order_id)),
                        ("state", "in", ["pending", "processing"]),
                    ],
                    limit=1,
                )
                if existing:
                    _logger.info(
                        "Queue already exists (queue_id=%s) for order_id=%s",
                        existing.id,
                        shopify_order_id,
                    )
                    return True

                # Also skip enqueue when order already imported (replay safety)
                existing_order = self.env["sale.order"].search(
                    [
                        ("shopify_order_id", "=", str(shopify_order_id)),
                        ("shopify_instance_id", "=", store.id),
                    ],
                    limit=1,
                )
                if existing_order:
                    _logger.info(
                        "Order already exists (so=%s) for shopify_order_id=%s — skip enqueue",
                        existing_order.id,
                        shopify_order_id,
                    )
                    return True

            queue_model.create(
                {
                    "store_id": store.id,
                    "shopify_order_id": str(shopify_order_id) if shopify_order_id else False,
                    "payload": json.dumps(payload),
                    "state": "pending",
                }
            )
            return True
        except Exception as e:
            _logger.exception("process_webhook_order failed: %s", str(e))
            raise
