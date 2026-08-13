import json
import logging

from odoo import http
from odoo.http import request


_logger = logging.getLogger(__name__)


class ShopifyWebhookController(http.Controller):
    @http.route(
        "/shopify/webhook/order",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def shopify_order_webhook(self, **kwargs):
        """Thin delegator: all receive/HMAC/dedup/route logic lives in the
        shared handler (shopify.webhook.handler.ingest_webhook)."""
        req = request.httprequest
        raw_data = req.get_data()
        shop_domain = req.headers.get("X-Shopify-Shop-Domain")
        hmac_header = req.headers.get("X-Shopify-Hmac-Sha256")
        webhook_id = req.headers.get("X-Shopify-Webhook-Id")
        topic = req.headers.get("X-Shopify-Topic") or "orders/create"
        return self._handle(raw_data, shop_domain, hmac_header, webhook_id, topic)

    @staticmethod
    def _json(body, status):
        return request.make_response(
            json.dumps(body), status=status, headers=[("Content-Type", "application/json")]
        )

    def _handle(self, raw_data, shop_domain, hmac_header, webhook_id, topic):
        handler = request.env["shopify.webhook.handler"].sudo()
        store = handler.get_store_for_webhook(shop_domain)
        if not store:
            request.env["shopify.sync.log.mixin"].create_log(
                store=False,
                log_type="order",
                message="Shopify webhook: unknown store %s" % (shop_domain or ""),
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
            )
            return request.make_response(
                "Unknown store", status=404, headers=[("Content-Type", "text/plain")]
            )

        try:
            result = handler.ingest_webhook(
                store=store,
                raw_data=raw_data,
                hmac_header=hmac_header,
                webhook_id=webhook_id,
                topic=topic,
                shop_domain=shop_domain,
            )
        except Exception as exc:
            _logger.exception("Shopify webhook processing failed: %s", exc)
            # HMAC already passed and the event is persisted as failed; ask Shopify
            # to retry with a 500 so the delivery is not lost.
            return self._json({"success": False, "error": "processing_error"}, 500)

        if not result.get("ok"):
            reason = result.get("reason") or "rejected"
            return request.make_response(
                reason, status=result.get("code", 403),
                headers=[("Content-Type", "text/plain")],
            )
        return self._json(
            {"success": True, "duplicate": bool(result.get("duplicate"))},
            result.get("code", 200),
        )

    @http.route(
        "/shopify/webhook/order_create",
        type="jsonrpc",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def shopify_order_create_webhook(self, **kwargs):
        """Alternate endpoint for orders/create and orders/updated webhooks."""
        topic = request.httprequest.headers.get("X-Shopify-Topic") or ""
        if topic not in ("orders/create", "orders/updated"):
            return {"success": False, "error": "Unsupported topic"}
        return self.shopify_order_webhook(**kwargs)

