import json

from odoo import _, api, fields, models


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
        string="Attempt",
        help="Retry attempt number for this operation when applicable.",
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


class ShopifySyncLogMixin(models.AbstractModel):
    _name = "shopify.sync.log.mixin"
    _description = "Shopify Sync Log Mixin"

    @api.model
    def _to_json_text(self, value):
        """Normalize payload/response values as JSON text for consistency."""
        if value in (None, False, ""):
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

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
            }
        )

