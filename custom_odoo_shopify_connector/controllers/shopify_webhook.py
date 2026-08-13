import json

from odoo import http
from odoo.http import request


def _ingest(default_topic):
    """Shared thin-delegator body for cancellation/refund webhook endpoints.

    All receive/HMAC/idempotency/routing logic lives in the shared handler
    (shopify.webhook.handler.ingest_webhook); controllers hold no business logic.
    """
    req = request.httprequest
    raw_data = req.get_data()
    shop_domain = req.headers.get("X-Shopify-Shop-Domain")
    hmac_header = req.headers.get("X-Shopify-Hmac-Sha256")
    webhook_id = req.headers.get("X-Shopify-Webhook-Id")
    topic = req.headers.get("X-Shopify-Topic") or default_topic
    handler = request.env["shopify.webhook.handler"].sudo()

    store = handler.get_store_for_webhook(shop_domain)
    if not store:
        request.env["shopify.sync.log.mixin"].create_log(
            store=False,
            log_type="order",
            message="Shopify %s webhook: unknown store %s" % (default_topic, shop_domain or ""),
            payload=raw_data.decode("utf-8", errors="ignore"),
            status="failed",
        )
        return request.make_response("Unknown store", status=404)

    try:
        result = handler.ingest_webhook(
            store=store,
            raw_data=raw_data,
            hmac_header=hmac_header,
            webhook_id=webhook_id,
            topic=topic,
            shop_domain=shop_domain,
        )
    except Exception:
        return request.make_response(
            json.dumps({"success": False, "error": "processing_error"}),
            status=500,
            headers=[("Content-Type", "application/json")],
        )

    if not result.get("ok"):
        return request.make_response(result.get("reason") or "rejected", status=result.get("code", 403))
    return request.make_response(
        json.dumps({"success": True, "duplicate": bool(result.get("duplicate"))}),
        status=result.get("code", 200),
        headers=[("Content-Type", "application/json")],
    )


class ShopifyCancellationWebhookController(http.Controller):
    @http.route(
        "/shopify/webhook/order_cancelled",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def shopify_order_cancelled_webhook(self, **kwargs):
        return _ingest("orders/cancelled")


class ShopifyRefundWebhookController(http.Controller):
    @http.route(
        "/shopify/webhook/refund_created",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def shopify_refund_created_webhook(self, **kwargs):
        return _ingest("refunds/create")
