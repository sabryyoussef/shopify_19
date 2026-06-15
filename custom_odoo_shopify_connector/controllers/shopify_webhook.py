import json

from odoo import _, fields, http
from odoo.http import request


class ShopifyCancellationWebhookController(http.Controller):
    @http.route(
        "/shopify/webhook/order_cancelled",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def shopify_order_cancelled_webhook(self, **kwargs):
        raw_data = request.httprequest.get_data()
        shop_domain = request.httprequest.headers.get("X-Shopify-Shop-Domain")
        hmac_header = request.httprequest.headers.get("X-Shopify-Hmac-Sha256")
        webhook_id = request.httprequest.headers.get("X-Shopify-Webhook-Id")
        topic = request.httprequest.headers.get("X-Shopify-Topic") or "orders/cancelled"
        handler = request.env["shopify.webhook.handler"].sudo()

        try:
            payload = json.loads(raw_data.decode("utf-8") or "{}")
        except Exception:
            request.env["shopify.sync.log.mixin"].create_log(
                store=False,
                log_type="order",
                message="Shopify orders/cancelled webhook: invalid JSON payload.",
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
            )
            return request.make_response("Invalid JSON", status=400)

        store = handler.get_store_for_webhook(shop_domain)
        if not store:
            request.env["shopify.sync.log.mixin"].create_log(
                store=False,
                log_type="order",
                message="Shopify orders/cancelled webhook: unknown store %s" % (shop_domain or ""),
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
            )
            return request.make_response("Unknown store", status=404)

        valid, reason = handler.validate_webhook_request(store, raw_data, hmac_header, webhook_id)
        if not valid:
            request.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="order",
                message="Shopify orders/cancelled webhook rejected: %s." % reason,
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
                response=json.dumps({"topic": topic, "webhook_id": webhook_id}),
            )
            status_code = 400 if "delivery id" in reason else 403
            return request.make_response(reason, status=status_code)

        event = handler.register_webhook_delivery(
            store=store,
            webhook_id=webhook_id,
            topic=topic,
            shop_domain=shop_domain,
            raw_payload=raw_data.decode("utf-8", errors="ignore")[:100000],
        )
        if webhook_id and not event:
            return request.make_response(json.dumps({"success": True, "duplicate": True}), status=200)

        shopify_order_id = str(payload.get("id") or payload.get("order_id") or "")
        cancel_reason = payload.get("cancel_reason") or payload.get("reason") or ""

        request.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message="Shopify webhook received: orders/cancelled",
            payload=raw_data.decode("utf-8", errors="ignore")[:5000],
            status="success",
            order_id=shopify_order_id or False,
        )

        if not shopify_order_id:
            handler.mark_webhook_event_status(event, "processed")
            return request.make_response(json.dumps({"success": True}), status=200)

        SaleOrder = request.env["sale.order"].sudo()
        order = SaleOrder.search(
            [
                ("shopify_order_id", "=", shopify_order_id),
                ("shopify_instance_id", "=", store.id),
            ],
            limit=1,
        )
        if not order:
            handler.mark_webhook_event_status(event, "processed")
            return request.make_response(json.dumps({"success": True}), status=200)

        if order.state == "cancel" or order.shopify_cancelled:
            handler.mark_webhook_event_status(event, "processed")
            return request.make_response(json.dumps({"success": True}), status=200)

        try:
            order.action_cancel()
        except Exception:
            order.write({"state": "cancel"})

        order.write(
            {
                "shopify_cancelled": True,
                "shopify_cancel_reason": cancel_reason or "shopify",
            }
        )

        request.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message="Odoo sale order cancelled from Shopify webhook.",
            payload=json.dumps({"odoo_order": order.name}),
            status="success",
            order_id=shopify_order_id,
        )

        handler.mark_webhook_event_status(event, "processed")

        return request.make_response(json.dumps({"success": True}), status=200)


class ShopifyRefundWebhookController(http.Controller):
    @http.route(
        "/shopify/webhook/refund_created",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def shopify_refund_created_webhook(self, **kwargs):
        raw_data = request.httprequest.get_data()
        shop_domain = request.httprequest.headers.get("X-Shopify-Shop-Domain")
        hmac_header = request.httprequest.headers.get("X-Shopify-Hmac-Sha256")
        webhook_id = request.httprequest.headers.get("X-Shopify-Webhook-Id")
        topic = request.httprequest.headers.get("X-Shopify-Topic") or "refunds/create"
        handler = request.env["shopify.webhook.handler"].sudo()

        try:
            payload = json.loads(raw_data.decode("utf-8") or "{}")
        except Exception:
            request.env["shopify.sync.log.mixin"].create_log(
                store=False,
                log_type="order",
                message="Shopify refunds/create webhook: invalid JSON payload.",
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
            )
            return request.make_response("Invalid JSON", status=400)

        store = handler.get_store_for_webhook(shop_domain)
        if not store:
            request.env["shopify.sync.log.mixin"].create_log(
                store=False,
                log_type="order",
                message="Shopify refunds/create webhook: unknown store %s" % (shop_domain or ""),
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
            )
            return request.make_response("Unknown store", status=404)

        valid, reason = handler.validate_webhook_request(store, raw_data, hmac_header, webhook_id)
        if not valid:
            request.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="order",
                message="Shopify refunds/create webhook rejected: %s." % reason,
                payload=raw_data.decode("utf-8", errors="ignore"),
                status="failed",
                response=json.dumps({"topic": topic, "webhook_id": webhook_id}),
            )
            status_code = 400 if "delivery id" in reason else 403
            return request.make_response(reason, status=status_code)

        event = handler.register_webhook_delivery(
            store=store,
            webhook_id=webhook_id,
            topic=topic,
            shop_domain=shop_domain,
            raw_payload=raw_data.decode("utf-8", errors="ignore")[:100000],
        )
        if webhook_id and not event:
            return request.make_response(json.dumps({"success": True, "duplicate": True}), status=200)

        shopify_order_id = str(payload.get("order_id") or payload.get("order", {}).get("id") or "")
        refund_id = str(payload.get("id") or "")

        request.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message="Shopify webhook received: refunds/create",
            payload=raw_data.decode("utf-8", errors="ignore")[:5000],
            status="success",
            order_id=shopify_order_id or False,
        )

        if not shopify_order_id:
            handler.mark_webhook_event_status(event, "processed")
            return request.make_response(json.dumps({"success": True}), status=200)

        SaleOrder = request.env["sale.order"].sudo()
        order = SaleOrder.search(
            [
                ("shopify_order_id", "=", shopify_order_id),
                ("shopify_instance_id", "=", store.id),
            ],
            limit=1,
        )
        if not order:
            handler.mark_webhook_event_status(event, "processed")
            return request.make_response(json.dumps({"success": True}), status=200)

        # Duplicate refund protection by Shopify refund id
        Move = request.env["account.move"].sudo()
        if refund_id and Move.search_count([("shopify_refund_id", "=", refund_id)]):
            handler.mark_webhook_event_status(event, "processed")
            return request.make_response(json.dumps({"success": True}), status=200)

        # Find a posted invoice to reverse
        invoice = Move.search(
            [
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("invoice_origin", "=", order.name),
            ],
            limit=1,
        )
        if not invoice:
            handler.mark_webhook_event_status(event, "processed")
            return request.make_response(json.dumps({"success": True}), status=200)

        try:
            # Create reversal credit note
            credit = invoice._reverse_moves(
                default_values_list=[{"ref": _("Shopify refund %s") % (refund_id or shopify_order_id)}]
            )
            if credit:
                credit.action_post()
                # Mark as synced and store Shopify refund id
                credit.write(
                    {
                        "shopify_refunded": True,
                        "shopify_refund_date": fields.Datetime.now(),
                        "shopify_refund_id": refund_id or False,
                    }
                )
                order.write({"shopify_refunded": True, "shopify_refund_date": fields.Datetime.now()})
        except Exception as exc:
            handler.mark_webhook_event_status(event, "failed", str(exc))
            raise

        handler.mark_webhook_event_status(event, "processed")

        return request.make_response(json.dumps({"success": True}), status=200)
