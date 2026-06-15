import json
import logging

from odoo import api, fields, models, _
from .license_mixin import license_is_active_strict
from ..services.order_service import OrderService
from ..services.order_import_service import OrderImportService
from ..services.fulfillment_service import ShopifyFulfillmentService
from ..services.retry_policy import classify_exception, next_retry_at


_logger = logging.getLogger(__name__)


class ShopifyOrderQueue(models.Model):
    _name = "shopify.order.queue"
    _inherit = ["shopify.queue.mixin"]
    _description = "Shopify Order Queue"
    _order = "create_date desc"

    _queue_state_field = "state"
    _queue_log_operation = "order"

    store_id = fields.Many2one(
        "shopify.store",
        required=True,
        ondelete="cascade",
        index=True,
    )
    shopify_order_id = fields.Char(string="Shopify Order ID", index=True)
    order_display_name = fields.Char(
        string="Order Name",
        compute="_compute_order_display_name",
    )
    payload = fields.Text()
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    log_message = fields.Text()
    job_type = fields.Selection(
        [("order", "Order")],
        string="Job Type",
        default="order",
        required=True,
    )
    retry_count = fields.Integer(default=0, index=True)
    next_retry_at = fields.Datetime(index=True)
    last_error = fields.Text()
    error_message = fields.Text()
    error_summary = fields.Char(
        string="Error Summary",
        compute="_compute_error_summary",
    )

    @api.depends("shopify_order_id", "store_id", "payload")
    def _compute_order_display_name(self):
        """Prefer the linked Odoo sale order name (e.g. MK1001), fallback to Shopify order name."""
        SaleOrder = self.env["sale.order"]

        store_ids = {q.store_id.id for q in self if q.store_id}
        shopify_ids = {q.shopify_order_id for q in self if q.shopify_order_id}

        sale_by_key = {}
        if store_ids and shopify_ids:
            # Map by (shopify_instance_id, shopify_order_id) -> sale.order.name
            sales = SaleOrder.search(
                [
                    ("shopify_instance_id", "in", list(store_ids)),
                    ("shopify_order_id", "in", list(shopify_ids)),
                ],
            )
            sale_by_key = {
                (s.shopify_instance_id.id, s.shopify_order_id): s.name for s in sales
            }

        for queue in self:
            key = (queue.store_id.id if queue.store_id else False, queue.shopify_order_id)
            # Fallback to Shopify `name` stored in the payload.
            shopify_name = False
            if queue.payload:
                try:
                    data = json.loads(queue.payload)
                except Exception:
                    data = {}
                shopify_name = data.get("name") or data.get("order_name") or False
                if not shopify_name and isinstance(data.get("order"), dict):
                    shopify_name = data["order"].get("name") or False

            queue.order_display_name = (
                sale_by_key.get(key) or shopify_name or queue.shopify_order_id or False
            )

    @api.depends("last_error", "error_message", "log_message")
    def _compute_error_summary(self):
        for queue in self:
            message = queue.last_error or queue.error_message or queue.log_message or ""
            message = " ".join(message.split())
            queue.error_summary = (message[:117] + "...") if len(message) > 120 else message

    @api.model
    def _claim_pending_queues(self, limit=50):
        """Atomically claim pending queues to avoid duplicate workers."""
        self.env.cr.execute(
            """
            WITH picked AS (
                SELECT id
                FROM shopify_order_queue
                WHERE state = 'pending'
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                ORDER BY create_date ASC, id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE shopify_order_queue q
            SET state = 'processing'
            FROM picked
            WHERE q.id = picked.id
            RETURNING q.id
            """,
            (limit,),
        )
        queue_ids = [row[0] for row in self.env.cr.fetchall()]
        return self.browse(queue_ids)

    @api.model
    def process_queue(self, limit=50):
        _logger.error("🕒 Order queue worker start (limit=%s)", limit)
        pending_queues = self._claim_pending_queues(limit=limit)
        import_service = OrderImportService(self.env)
        order_service = OrderService(self.env, import_service=import_service)
        fulfillment_service = ShopifyFulfillmentService(self.env)
        max_retries = 3

        _logger.error("🧾 Claimed %s pending queue item(s)", len(pending_queues))
        for queue in pending_queues:
            try:
                _logger.error("🚀 Processing queue ID: %s", queue.id)
                _logger.error("Payload: %s", (queue.payload or "")[:1000])
                with self.env.cr.savepoint():
                    payload = {}
                    if queue.payload:
                        try:
                            payload = json.loads(queue.payload)
                        except Exception:
                            payload = {}

                    imported, skip_message = import_service.import_shopify_order(
                        store=queue.store_id,
                        order_data=payload or {},
                        queue=queue,
                        order_service=order_service,
                        fulfillment_service=fulfillment_service,
                    )
                    if not imported:
                        _logger.error(
                            "⚠️ Queue skipped newly added ..........(queue_id=%s store_id=%s shopify_order_id=%s): %s",
                            queue.id,
                            queue.store_id.id if queue.store_id else None,
                            payload.get("id") or payload.get("order_id") if isinstance(payload, dict) else None,
                            skip_message,
                        )
                        queue.write(
                            {
                                "state": "done",
                                "log_message": skip_message
                                or _(
                                    "Order skipped: no workflow could be resolved from mapping or fallback configuration."
                                ),
                            }
                        )
                        continue

                    queue.write(
                        {
                            "state": "done",
                            "log_message": _("Processed successfully"),
                            "retry_count": 0,
                            "error_message": False,
                            "last_error": False,
                            "next_retry_at": False,
                        }
                    )
                    _logger.error("✅ Queue processed successfully (queue_id=%s)", queue.id)
            except Exception as e:
                _logger.exception("❌ Queue processing failed: %s", str(e))
                transient, _error_kind, error_message = classify_exception(e)
                values = {
                    "log_message": str(e) or error_message,
                    "error_message": error_message,
                    "last_error": error_message,
                }
                if transient and queue.retry_count < max_retries:
                    values.update(
                        {
                            "state": "pending",
                            "retry_count": queue.retry_count + 1,
                            "next_retry_at": next_retry_at(queue.retry_count + 1),
                        }
                    )
                else:
                    values.update({"state": "failed", "next_retry_at": False})
                queue.write(values)
                self.env["shopify.sync.log.mixin"].create_log(
                    store=queue.store_id,
                    log_type="order",
                    message=error_message,
                    payload=queue.payload,
                    status="failed",
                )
                _logger.error(
                    "❌ Queue ended in state=%s (queue_id=%s)",
                    values.get("state"),
                    queue.id,
                )

        _logger.error("✅ Order queue worker end")

    @api.model
    def cron_process_order_queue(self):
        if not license_is_active_strict(self.env):
            _logger.warning("License inactive - cron skipped (order queue)")
            return
        self.process_queue()

    def action_open_related_records(self):
        self.ensure_one()
        domain = [("shopify_instance_id", "=", self.store_id.id)]
        if self.shopify_order_id:
            domain.append(("shopify_order_id", "=", self.shopify_order_id))
        return {
            "type": "ir.actions.act_window",
            "name": _("Related Orders"),
            "res_model": "sale.order",
            "view_mode": "list,form",
            "domain": domain,
        }

    def action_fetch_orders(self):
        """Manual: fetch/enqueue Shopify orders for this store."""
        self.ensure_one()
        sync = self.env["shopify.order.sync"]
        sync.sync_orders(self.store_id)
        return {"type": "ir.actions.client", "tag": "reload"}

