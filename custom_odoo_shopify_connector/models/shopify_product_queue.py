from odoo import _, api, fields, models

from ..services.queue_service import ProductQueueService
from .license_mixin import license_is_active_strict


class ShopifyProductQueue(models.Model):
    _name = "shopify.product.queue"
    _description = "Shopify Product Queue (Batch)"
    _order = "create_date desc"
    name = fields.Char(
        string="Queue Reference",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )
    store_id = fields.Many2one(
        "shopify.store",
        string="Store",
        required=True,
        ondelete="cascade",
    )
    do_not_update_existing = fields.Boolean(
        string="Do Not Update Existing Products",
        default=False,
    )
    import_based_on = fields.Selection(
        [
            ("create_date", "Create Date"),
            ("update_date", "Update Date"),
        ],
        string="Import Based On",
        default="create_date",
        readonly=True,
    )
    from_date = fields.Datetime(string="From Date", readonly=True)
    to_date = fields.Datetime(string="To Date", readonly=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="Status",
        default="pending",
        required=True,
        index=True,
        compute="_compute_state",
        store=True,
        readonly=True,
    )
    line_ids = fields.One2many(
        "shopify.product.queue.line",
        "queue_id",
        string="Product Lines",
        copy=False,
    )
    total_records = fields.Integer(
        string="Total Records",
        compute="_compute_record_counts",
        store=True,
    )
    done_records = fields.Integer(
        string="Done",
        compute="_compute_record_counts",
        store=True,
    )
    failed_records = fields.Integer(
        string="Failed",
        compute="_compute_record_counts",
        store=True,
    )
    draft_records = fields.Integer(
        string="Draft",
        compute="_compute_record_counts",
        store=True,
    )
    error_summary = fields.Char(
        string="Error Summary",
        compute="_compute_error_summary",
    )

    @api.depends("line_ids.state")
    def _compute_record_counts(self):
        for rec in self:
            lines = rec.line_ids
            rec.total_records = len(lines)
            rec.done_records = len(lines.filtered(lambda l: l.state == "done"))
            rec.failed_records = len(lines.filtered(lambda l: l.state == "failed"))
            rec.draft_records = len(lines.filtered(lambda l: l.state == "pending"))

    @api.depends("line_ids.state")
    def _compute_state(self):
        for rec in self:
            total = len(rec.line_ids)
            if total == 0:
                rec.state = "pending"
            elif rec.draft_records == total:
                rec.state = "pending"
            elif any(line.state == "processing" for line in rec.line_ids):
                rec.state = "processing"
            elif rec.draft_records > 0:
                rec.state = "processing"
            elif rec.failed_records > 0:
                rec.state = "failed"
            else:
                rec.state = "done"

    @api.depends("line_ids.state", "line_ids.message")
    def _compute_error_summary(self):
        for rec in self:
            failed_line = rec.line_ids.filtered(lambda l: l.state == "failed")[:1]
            message = failed_line.message if failed_line else ""
            message = " ".join((message or "").split())
            if not message and rec.failed_records:
                message = _("One or more queue lines failed.")
            rec.error_summary = (message[:117] + "...") if len(message) > 120 else message

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shopify.product.queue"
                ) or _("New")
        return super().create(vals_list)

    def action_process_manually(self):
        """Process pending lines of selected queue(s)."""
        service = ProductQueueService(self.env)
        for queue in self:
            service.process_queue(queue)
        return True

    def action_set_to_completed(self):
        """Mark all pending lines as done and set queue to completed."""
        for queue in self:
            queue.line_ids.filtered(lambda l: l.state == "pending").write(
                {"state": "done", "message": "Manually set to completed"}
            )
        return True

    def action_fetch_products(self):
        """Fetch products from Shopify and process pending lines immediately."""
        service = ProductQueueService(self.env)
        for queue in self:
            service.fetch_products_into_queue(queue)
            # Users expect "Fetch Products" to advance queue work now, not only
            # create pending lines for a later cron/manual run.
            service.process_queue(queue)
        return True

    @api.model
    def process_draft_queues_cron(self):
        """Cron: process all queues that have pending lines."""
        if not license_is_active_strict(self.env):
            import logging

            logging.getLogger(__name__).warning(
                "License inactive - cron skipped (product queue)"
            )
            return
        service = ProductQueueService(self.env)
        return service.process_draft_queues()

    def action_retry_failed(self):
        for queue in self:
            queue.line_ids.filtered(lambda l: l.state == "failed").write(
                {
                    "state": "pending",
                    "message": False,
                }
            )
        return True

    def action_reset_to_pending(self):
        for queue in self:
            queue.line_ids.write(
                {
                    "state": "pending",
                    "message": False,
                }
            )
        return True

    def action_open_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Queue Sync Log"),
            "res_model": "shopify.sync.log",
            "view_mode": "list,form",
            "domain": [
                ("store_id", "=", self.store_id.id),
                ("operation", "=", "product"),
                ("date", ">=", self.create_date),
            ],
            "context": {"search_default_failed": 1},
        }

    def action_open_related_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Related Queue Lines"),
            "res_model": "shopify.product.queue.line",
            "view_mode": "list,form",
            "domain": [("queue_id", "=", self.id)],
        }
