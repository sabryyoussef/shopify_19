import json

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from ..services.retry_policy import classify_exception, next_retry_at


class ShopifyImportQueue(models.Model):
    _name = "shopify.import.queue"
    _inherit = ["shopify.queue.mixin"]
    _description = "Shopify Import Queue"
    _order = "create_date desc, id desc"

    _queue_state_field = "status"
    _queue_log_operation = "order"

    store_id = fields.Many2one("shopify.store", string="Store", required=True, index=True, ondelete="cascade")
    operation_type = fields.Selection(
        [
            ("import_shipped_orders", "Import Shipped Orders"),
        ],
        required=True,
        index=True,
    )
    status = fields.Selection(
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
    start_date = fields.Datetime(required=True, index=True)
    end_date = fields.Datetime(required=True, index=True)
    records_to_process = fields.Integer(default=0)
    processed_records = fields.Integer(default=0)
    error_message = fields.Text()
    retry_count = fields.Integer(default=0, index=True)
    next_retry_at = fields.Datetime(index=True)
    last_error = fields.Text()
    error_summary = fields.Char(
        string="Error Summary",
        compute="_compute_error_summary",
    )

    @api.depends("last_error", "error_message")
    def _compute_error_summary(self):
        for queue in self:
            message = queue.last_error or queue.error_message or ""
            message = " ".join(message.split())
            queue.error_summary = (message[:117] + "...") if len(message) > 120 else message

    def _claim_for_processing(self):
        """Try to atomically claim this queue if still pending."""
        self.ensure_one()
        self.env.cr.execute(
            """
            UPDATE shopify_import_queue
            SET status = 'processing'
            WHERE id = %s
              AND status = 'pending'
              AND (next_retry_at IS NULL OR next_retry_at <= NOW())
            RETURNING id
            """,
            (self.id,),
        )
        return bool(self.env.cr.fetchone())

    def action_process_queue_manually(self):
        for queue in self:
            queue._process_queue()
        return True

    def _process_queue(self):
        self.ensure_one()
        if self.status in ("done", "failed"):
            return

        claimed = self._claim_for_processing()
        if not claimed:
            self.invalidate_recordset(["status"])
            return

        self.error_message = False
        self.processed_records = 0
        self.last_error = False

        max_retries = 3
        try:
            if self.operation_type == "import_shipped_orders":
                self.env["shopify.service"].import_shipped_orders_from_queue(self)
            else:
                raise UserError(_("Unsupported queue operation type: %s") % self.operation_type)
            self.status = "done"
            self.retry_count = 0
            self.next_retry_at = False
        except Exception as e:
            transient, _, error_message = classify_exception(e)
            next_attempt = self.retry_count + 1
            values = {
                "error_message": error_message,
                "last_error": error_message,
            }
            if transient and self.retry_count < max_retries:
                values.update(
                    {
                        "status": "pending",
                        "retry_count": next_attempt,
                        "next_retry_at": next_retry_at(next_attempt),
                    }
                )
            else:
                values.update({"status": "failed", "next_retry_at": False})
            self.write(values)
            self.env["shopify.sync.log.mixin"].create_log(
                store=self.store_id,
                log_type="order",
                message=_("Queue processing failed: %s") % error_message,
                payload=json.dumps(
                    {
                        "queue_id": self.id,
                        "operation_type": self.operation_type,
                        "start_date": fields.Datetime.to_string(self.start_date),
                        "end_date": fields.Datetime.to_string(self.end_date),
                    }
                ),
                status="failed",
            )

    def action_open_related_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Related Orders"),
            "res_model": "sale.order",
            "view_mode": "list,form",
            "domain": [
                ("shopify_instance_id", "=", self.store_id.id),
                ("create_date", ">=", self.start_date),
                ("create_date", "<=", self.end_date),
            ],
        }

