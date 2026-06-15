import base64
import hashlib
import hmac
import json
import logging
from urllib.parse import urlparse

from odoo import api, fields, models


_logger = logging.getLogger(__name__)


class ShopifyWebhookEvent(models.Model):
    _name = "shopify.webhook.event"
    _description = "Shopify Webhook Event"
    _order = "create_date desc"

    store_id = fields.Many2one("shopify.store", required=True, index=True)
    webhook_id = fields.Char(required=True, index=True)
    topic = fields.Char(index=True)
    shop_domain = fields.Char(index=True)
    payload = fields.Text(help="Raw webhook payload for replay diagnostics.")
    status = fields.Selection(
        [("received", "Received"), ("processed", "Processed"), ("failed", "Failed")],
        required=True,
        default="received",
    )
    error_message = fields.Text()

    _sql_constraints = [
        (
            "shopify_webhook_event_unique_delivery",
            "unique(store_id, webhook_id)",
            "This Shopify webhook delivery was already received.",
        )
    ]


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
                "status": "received",
            }
        )

    @api.model
    def mark_webhook_event_status(self, event, status, error_message=None):
        if not event:
            return False
        values = {"status": status}
        if error_message:
            values["error_message"] = error_message
        return event.sudo().write(values)

    @api.model
    def process_webhook_order(self, payload, store, shop_domain=None, is_valid_hmac=None):
        try:
            _logger.error("➡️ Processing Shopify order webhook")

            _logger.error("Shop domain header: %s", shop_domain)
            _logger.error("HMAC valid: %s", is_valid_hmac)
            try:
                _logger.error("Raw Payload (truncated): %s", json.dumps(payload)[:1000])
            except Exception:
                _logger.error("Raw Payload (truncated): %s", str(payload)[:1000])

            if not store:
                _logger.error("❌ No matching store for domain: %s", shop_domain)
                return False

            _logger.error("Webhook enabled (manage_orders_webhook): %s", bool(store.manage_orders_webhook))

            if not store.manage_orders_webhook:
                _logger.error("❌ Webhook disabled for store")
                return False

            shopify_order_id = payload.get("id") or payload.get("order_id")
            _logger.error("🧾 Incoming Shopify order_id: %s", shopify_order_id)

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
                    _logger.error(
                        "⏭️ Queue already exists (queue_id=%s) for order_id=%s",
                        existing.id,
                        shopify_order_id,
                    )
                    return True

            _logger.error("📝 Creating queue record for order: %s", shopify_order_id)
            queue = queue_model.create(
                {
                    "store_id": store.id,
                    "shopify_order_id": str(shopify_order_id) if shopify_order_id else False,
                    "payload": json.dumps(payload),
                    "state": "pending",
                }
            )
            _logger.error(
                "📝 Queue record created: queue_id=%s order_id=%s",
                queue.id,
                shopify_order_id,
            )
            return True
        except Exception as e:
            _logger.exception("❌ process_webhook_order failed: %s", str(e))
            raise

