import re
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


_SECRET_KEY_RE = re.compile(
    r"(access[_\s-]?token|authorization|api[_\s-]?secret|client[_\s-]?secret|"
    r"password|passwd|webhook[_\s-]?secret|payment[_\s-]?(?:token|credential|key)|"
    r"x-shopify-access-token|bearer)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(authorization|access[_-]?token|api[_-]?secret|x-shopify-access-token)"
    r"\s*[:=]\s*['\"]?([^\s'\",}]+)"
)


class ShopifySyncLog(models.Model):
    _name = "shopify.sync.log"
    _description = "Shopify Synchronization Log"
    _order = "create_date desc"

    name = fields.Char(string="Name", default=lambda self: _("Shopify Sync Log"))
    shopify_id = fields.Char(string="Shopify Resource ID", index=True)
    store_id = fields.Many2one("shopify.store", string="Store")
    queue_id = fields.Integer(
        string="Queue ID",
        index=True,
        help="Related queue record id when the log is emitted from queue processing.",
    )
    order_id = fields.Char(
        string="Shopify Order ID",
        index=True,
        help="Shopify order id related to this log entry (when applicable).",
    )
    webhook_id = fields.Char(
        string="Webhook ID",
        index=True,
        help="Shopify webhook delivery id when this log originates from a webhook.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        help="Odoo product related to this log entry (when applicable).",
    )
    instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        related="store_id",
        store=True,
        readonly=True,
    )
    operation = fields.Selection(
        [
            ("connection", "Connection"),
            ("product", "Product"),
            ("customer", "Customer"),
            ("order", "Order"),
            ("refund", "Refund"),
            ("inventory", "Inventory"),
            ("shipping", "Shipping"),
            ("fulfillment", "Fulfillment"),
            ("other", "Other"),
        ],
        string="Operation",
        required=True,
        default="other",
    )
    message = fields.Text(required=True)
    payload = fields.Text()
    response = fields.Text(string="API Response", help="Raw API response or error details.")
    attempt = fields.Integer(
        string="Attempt Count",
        help="Retry attempt number for this operation when applicable.",
    )
    last_attempt_at = fields.Datetime(
        string="Last Attempt",
        help="Timestamp of the latest processing/retry attempt.",
    )
    duration_ms = fields.Integer(
        string="Duration (ms)",
        help="Execution duration in milliseconds when available.",
    )
    error_type = fields.Char(
        string="Error Type",
        index=True,
        help="Normalized error classification (for example: timeout, rate_limit, validation).",
    )
    error_message = fields.Text(
        string="Error Message",
        help="Human-readable error details when status is failed.",
    )
    status = fields.Selection(
        [("success", "Success"), ("failed", "Failed")],
        required=True,
        default="success",
    )
    timestamp = fields.Datetime(
        string="Timestamp",
        default=lambda self: fields.Datetime.now(),
        required=True,
        index=True,
        help="When this log entry was created (functional timestamp for reporting).",
    )
    # Backward compatibility: keep existing column used by older data / views.
    date = fields.Datetime(
        string="Date",
        default=lambda self: fields.Datetime.now(),
        required=True,
        help="Deprecated: use Timestamp instead.",
    )

    def action_retry(self):
        """Retry only failed logs that are tied to a webhook or queue entry."""
        for log in self:
            if log.status != "failed":
                raise UserError(_("Only failed sync logs can be retried."))
            if log.webhook_id and log.store_id:
                event = self.env["shopify.webhook.event"].sudo().search(
                    [
                        ("store_id", "=", log.store_id.id),
                        ("webhook_id", "=", log.webhook_id),
                    ],
                    limit=1,
                )
                if not event:
                    raise UserError(_("No webhook event found for webhook id %s.") % log.webhook_id)
                if event.status == "processing":
                    raise UserError(_("Related webhook is already being processed."))
                self.env["shopify.webhook.handler"].replay_webhook_event(event)
                log.write(
                    {
                        "attempt": (log.attempt or 0) + 1,
                        "last_attempt_at": fields.Datetime.now(),
                    }
                )
                continue
            if log.queue_id:
                queue = self.env["shopify.order.queue"].sudo().browse(log.queue_id)
                if queue.exists() and hasattr(queue, "action_retry_failed"):
                    queue.action_retry_failed()
                    log.write(
                        {
                            "attempt": (log.attempt or 0) + 1,
                            "last_attempt_at": fields.Datetime.now(),
                        }
                    )
                    continue
            raise UserError(
                _("This log cannot be retried (missing webhook id / queue id).")
            )
        return True


class ShopifySyncLogMixin(models.AbstractModel):
    _name = "shopify.sync.log.mixin"
    _description = "Shopify Sync Log Mixin"

    @api.model
    def mask_secrets(self, value):
        """Mask tokens/secrets in JSON/text before persisting to sync logs."""
        if value in (None, False, ""):
            return ""
        text = value if isinstance(value, str) else None
        data = None
        if not isinstance(value, str):
            data = value
        else:
            try:
                data = json.loads(value)
            except Exception:
                data = None

        def _mask_obj(obj):
            if isinstance(obj, dict):
                out = {}
                for key, val in obj.items():
                    if _SECRET_KEY_RE.search(str(key or "")):
                        out[key] = "***MASKED***"
                    else:
                        out[key] = _mask_obj(val)
                return out
            if isinstance(obj, list):
                return [_mask_obj(v) for v in obj]
            if isinstance(obj, str) and len(obj) > 20 and _SECRET_KEY_RE.search(obj):
                return "***MASKED***"
            return obj

        if data is not None:
            try:
                return json.dumps(_mask_obj(data), ensure_ascii=False, default=str)
            except Exception:
                pass

        text = text if text is not None else str(value)
        text = _SECRET_VALUE_RE.sub(r"\1=***MASKED***", text)
        return text

    @api.model
    def _to_json_text(self, value):
        """Normalize payload/response values as JSON text for consistency."""
        if value in (None, False, ""):
            return ""
        if isinstance(value, str):
            return self.mask_secrets(value)
        try:
            return self.mask_secrets(json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            return self.mask_secrets(str(value))

    @api.model
    def create_log(
        self,
        store,
        log_type,
        message,
        payload=None,
        status="success",
        response=None,
        order_id=None,
        queue_id=None,
        shopify_id=None,
        attempt=None,
        duration_ms=None,
        error_type=None,
        webhook_id=None,
        error_message=None,
        last_attempt_at=None,
    ):
        # Always use sudo to ensure that technical logging never fails with
        # AccessError for regular users or background jobs.
        return self.env["shopify.sync.log"].sudo().create(
            {
                "store_id": store.id if store else False,
                "order_id": str(order_id) if order_id else False,
                "operation": log_type,
                "message": message,
                "payload": self._to_json_text(payload),
                "response": self._to_json_text(response),
                "status": status,
                "queue_id": int(queue_id) if queue_id else False,
                "shopify_id": str(shopify_id) if shopify_id else False,
                "attempt": int(attempt) if attempt else False,
                "duration_ms": int(duration_ms) if duration_ms else False,
                "error_type": (error_type or "").strip() or False,
                "webhook_id": str(webhook_id) if webhook_id else False,
                "error_message": error_message or (message if status == "failed" else False),
                "last_attempt_at": last_attempt_at or False,
            }
        )
