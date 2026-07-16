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
    # v44m-a3hs-qc6u
    def shopify_order_webhook(self, **kwargs):
        _logger.error("🔥 WEBHOOK RECEIVED")
        _logger.error("Headers: %s", dict(request.httprequest.headers))
        _logger.error("Raw Body: %s", request.httprequest.data[:1000])

        raw_data = request.httprequest.get_data()
        shop_domain = request.httprequest.headers.get("X-Shopify-Shop-Domain")
        hmac_header = request.httprequest.headers.get("X-Shopify-Hmac-Sha256")
        webhook_id = request.httprequest.headers.get("X-Shopify-Webhook-Id")
        topic = request.httprequest.headers.get("X-Shopify-Topic") or "orders/create"
        handler = request.env["shopify.webhook.handler"].sudo()

        try:
            payload = json.loads(raw_data.decode("utf-8") or "{}")
        except Exception:
            request.env["shopify.sync.log.mixin"].create_log(
                store=False,
                log_type="order",
                message="Shopify webhook: invalid JSON payload.",
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
            )
            return request.make_response(
                "Invalid JSON", status=400, headers=[("Content-Type", "text/plain")]
            )

        store = handler.get_store_for_webhook(shop_domain)
        normalized_shop_domain = handler._normalize_shop_domain(shop_domain)
        _logger.error(
            "Shop domain resolution: header=%s normalized=%s store_found=%s",
            shop_domain,
            normalized_shop_domain,
            bool(store),
        )
        if not store:
            _logger.error("❌ No matching store for domain: %s", shop_domain)
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

        valid, reason = handler.validate_webhook_request(store, raw_data, hmac_header, webhook_id)
        _logger.error("HMAC valid: %s", valid)
        if not valid:
            request.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="order",
                message="Shopify webhook rejected: %s." % reason,
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
                response=json.dumps({"topic": topic, "webhook_id": webhook_id}),
            )
            status_code = 400 if "delivery id" in reason else 403
            return request.make_response(
                reason,
                status=status_code,
                headers=[("Content-Type", "text/plain")],
            )

        event = handler.register_webhook_delivery(
            store=store,
            webhook_id=webhook_id,
            topic=topic,
            shop_domain=shop_domain,
            raw_payload=raw_data.decode("utf-8", errors="ignore")[:100000],
        )
        if webhook_id and not event:
            request.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="order",
                message="Duplicate Shopify webhook delivery ignored.",
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="success",
                response=json.dumps({"topic": topic, "webhook_id": webhook_id, "duplicate": True}),
                order_id=payload.get("id") or payload.get("order_id"),
            )
            return request.make_response(
                json.dumps({"success": True, "duplicate": True}),
                status=200,
                headers=[("Content-Type", "application/json")],
            )

        try:
            from ..services import lifecycle_logger as llog

            handler.process_webhook_order(
                payload,
                store,
                shop_domain=shop_domain,
                is_valid_hmac=valid,
                operation=llog.operation_from_topic(topic),
                webhook_id=webhook_id,
                correlation_id="wh-%s" % (webhook_id or "") if webhook_id else None,
            )
            _logger.error("✅ Webhook processed successfully")
        except Exception as exc:
            _logger.exception("❌ Webhook failed: %s", str(exc))
            handler.mark_webhook_event_status(event, "failed", str(exc))
            raise
        handler.mark_webhook_event_status(event, "processed")

        return request.make_response(
            json.dumps({"success": True}),
            status=200,
            headers=[("Content-Type", "application/json")],
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

