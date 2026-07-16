import json
import logging

from odoo import api, fields, models, _
from .license_mixin import license_is_active_strict
from ..services.order_service import OrderService
from ..services.order_import_service import OrderImportService
from ..services.order_update_service import OrderUpdateService
from ..services.fulfillment_service import ShopifyFulfillmentService
from ..services.retry_policy import classify_exception, next_retry_at
from ..services import lifecycle_logger as llog


_logger = logging.getLogger(__name__)

# Operations that are handled by the create/update sync services in this phase.
_ORDER_LIFECYCLE_OPS = (llog.OP_ORDER, llog.OP_CREATE, llog.OP_UPDATE)


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
        [
            ("order", "Order (legacy / auto)"),
            ("create", "Create"),
            ("update", "Update"),
            ("fulfillment", "Fulfillment"),
            ("payment", "Payment"),
            ("refund", "Refund"),
            ("cancellation", "Cancellation"),
        ],
        string="Job Type",
        default="order",
        required=True,
        help=(
            "Lifecycle operation for this queue item. 'order' is the legacy/auto "
            "value: the processor decides create-vs-update at runtime. Explicit "
            "'create'/'update' route directly to the matching sync service."
        ),
    )
    correlation_id = fields.Char(
        string="Correlation ID",
        index=True,
        help="Stable id used to trace this order across webhook, queue, sale order, "
        "invoice and payment lifecycle logs.",
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

    def _normalized_operation(self):
        """Return the lifecycle operation for this queue item."""
        self.ensure_one()
        return self.job_type or llog.OP_ORDER

    def _dispatch_queue(
        self,
        queue,
        payload,
        trace,
        import_service,
        order_service,
        update_service,
        fulfillment_service,
    ):
        """Route a claimed queue item to the correct lifecycle handler.

        Returns a tuple ``(processed_ok, message)``. ``processed_ok`` False means
        the item is intentionally a no-op (idempotent skip / deferred operation).
        """
        store = queue.store_id
        op = queue._normalized_operation()

        # Operations reserved for later phases (fulfillment/payment/refund/
        # cancellation) are recognised so the queue is extensible, but they are
        # not driven from here in this phase.
        if op not in _ORDER_LIFECYCLE_OPS:
            return (
                False,
                _("Operation '%s' is queued but handled by a later lifecycle phase.") % op,
            )

        existing = order_service._find_existing_order(payload, store)
        if existing:
            trace.bind(so=existing.name)
            trace.step(
                llog.STEP_EXISTING_ORDER_FOUND,
                so=existing.name,
                msg="state=%s" % existing.state,
            )

        # Decide the effective operation. Legacy/auto ('order') becomes an update
        # when the order already exists, otherwise a create.
        if op == llog.OP_CREATE:
            effective = llog.OP_CREATE
        elif op == llog.OP_UPDATE:
            effective = llog.OP_UPDATE
        else:
            effective = llog.OP_UPDATE if existing else llog.OP_CREATE
        trace.op = effective

        if effective == llog.OP_CREATE:
            if existing:
                # Idempotency: never create a duplicate and never re-run the
                # aggressive workflow for an already-imported order.
                return (
                    True,
                    _("Duplicate create ignored; order already imported as %s.")
                    % existing.name,
                )
            imported, skip_message = import_service.import_shopify_order(
                store=store,
                order_data=payload,
                queue=queue,
                order_service=order_service,
                fulfillment_service=fulfillment_service,
            )
            if not imported:
                return (False, skip_message or _("Order skipped: no workflow resolved."))
            new_order = order_service._find_existing_order(payload, store)
            if new_order:
                trace.bind(so=new_order.name)
                trace.step(llog.STEP_SO_CREATED, so=new_order.name)
            return (True, _("Order created/imported."))

        # effective == UPDATE
        if not existing:
            # Out-of-order update: the order was never imported. Import it so no
            # data is lost (webhooks can arrive out of order).
            imported, skip_message = import_service.import_shopify_order(
                store=store,
                order_data=payload,
                queue=queue,
                order_service=order_service,
                fulfillment_service=fulfillment_service,
            )
            if not imported:
                return (False, skip_message or _("Update for unknown order skipped."))
            new_order = order_service._find_existing_order(payload, store)
            if new_order:
                trace.bind(so=new_order.name)
                trace.step(
                    llog.STEP_SO_CREATED,
                    so=new_order.name,
                    msg="update-for-unknown-order imported as new",
                )
            return (True, _("Update received for not-yet-imported order; imported as new."))

        # Existing order + update -> route to the update sync service.
        # This never re-runs the create workflow (no duplicate invoice/payment).
        update_service.update_order_from_payload(existing, payload, store, trace=trace)
        return (True, _("Existing order %s updated via update sync.") % existing.name)

    @api.model
    def process_queue(self, limit=50):
        _logger.info("Order queue worker start (limit=%s)", limit)
        pending_queues = self._claim_pending_queues(limit=limit)
        import_service = OrderImportService(self.env)
        order_service = OrderService(self.env, import_service=import_service)
        update_service = OrderUpdateService(self.env, import_service=import_service)
        fulfillment_service = ShopifyFulfillmentService(self.env)
        max_retries = 3

        _logger.info("Claimed %s pending queue item(s)", len(pending_queues))
        for queue in pending_queues:
            correlation_id = queue.correlation_id or llog.new_correlation_id()
            if not queue.correlation_id:
                queue.correlation_id = correlation_id
            trace = llog.LifecycleTrace(
                correlation_id=correlation_id,
                op=queue._normalized_operation(),
                shop_order=queue.shopify_order_id,
                queue=queue.id,
            )
            try:
                with self.env.cr.savepoint():
                    payload = {}
                    if queue.payload:
                        try:
                            payload = json.loads(queue.payload)
                        except Exception:
                            payload = {}
                    trace.bind(shop_name=(payload or {}).get("name"))
                    trace.step(llog.STEP_QUEUE_PROCESSING)

                    processed_ok, message = self._dispatch_queue(
                        queue,
                        payload or {},
                        trace,
                        import_service=import_service,
                        order_service=order_service,
                        update_service=update_service,
                        fulfillment_service=fulfillment_service,
                    )
                    if not processed_ok:
                        queue.write({"state": "done", "log_message": message or _("Skipped")})
                        trace.step(llog.STEP_SKIPPED, status="skipped", msg=message)
                        continue

                    queue.write(
                        {
                            "state": "done",
                            "log_message": message or _("Processed successfully"),
                            "retry_count": 0,
                            "error_message": False,
                            "last_error": False,
                            "next_retry_at": False,
                        }
                    )
                    trace.step(llog.STEP_COMPLETED, msg=message)
            except Exception as e:
                _logger.exception("Queue processing failed (queue_id=%s): %s", queue.id, e)
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
                    queue.write(values)
                    trace.step(
                        llog.STEP_RETRY_SCHEDULED,
                        status="retry",
                        msg="attempt=%s error=%s" % (queue.retry_count, error_message),
                    )
                else:
                    values.update({"state": "failed", "next_retry_at": False})
                    queue.write(values)
                    trace.step(
                        llog.STEP_FAILURE,
                        status="failed",
                        level=logging.ERROR,
                        msg=error_message,
                    )
                self.env["shopify.sync.log.mixin"].create_log(
                    store=queue.store_id,
                    log_type="order",
                    message=error_message,
                    payload=queue.payload,
                    status="failed",
                    queue_id=queue.id,
                    shopify_id=queue.shopify_order_id,
                    correlation_id=trace.correlation_id,
                )

        _logger.info("Order queue worker end")

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

