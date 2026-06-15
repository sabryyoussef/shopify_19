from odoo import _, api, fields, models

from .license_mixin import license_is_active_strict


class ShopifyCustomerImportQueue(models.Model):
    _name = "shopify.customer.import.queue"
    _inherit = ["shopify.queue.mixin"]
    _description = "Shopify Customer Import Queue"
    _order = "create_date desc"

    _queue_state_field = "status"
    _queue_log_operation = "customer"

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
    operation_type = fields.Selection(
        [
            ("import_customers", "Import Customers"),
        ],
        string="Operation Type",
        default="import_customers",
        required=True,
    )
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="Status",
        default="pending",
        required=True,
    )
    records_to_process = fields.Integer(string="Records To Process")
    processed_records = fields.Integer(string="Processed Records")
    error_message = fields.Text(string="Error Message")
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
            UPDATE shopify_customer_import_queue
            SET status = 'processing'
            WHERE id = %s
              AND status = 'pending'
              AND (next_retry_at IS NULL OR next_retry_at <= NOW())
            RETURNING id
            """,
            (self.id,),
        )
        return bool(self.env.cr.fetchone())

    @api.model
    def _claim_pending_queues(self, limit=5):
        """Atomically claim pending customer queues for cron workers."""
        self.env.cr.execute(
            """
            WITH picked AS (
                SELECT id
                FROM shopify_customer_import_queue
                WHERE status = 'pending'
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                ORDER BY create_date ASC, id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE shopify_customer_import_queue q
            SET status = 'processing'
            FROM picked
            WHERE q.id = picked.id
            RETURNING q.id
            """,
            (limit,),
        )
        queue_ids = [row[0] for row in self.env.cr.fetchall()]
        return self.browse(queue_ids)

    def action_process_manually(self):
        """Button: Process Queue Manually."""
        for queue in self:
            if queue._claim_for_processing():
                self.env["shopify.service"].import_customers_from_queue(queue)
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shopify.customer.import.queue"
                ) or _("New")
        return super().create(vals_list)

    @api.model
    def cron_process_pending_queues(self):
        """Cron: automatically process pending customer import queues."""
        if not license_is_active_strict(self.env):
            import logging

            logging.getLogger(__name__).warning(
                "License inactive - cron skipped (customer import queues)"
            )
            return
        pending = self._claim_pending_queues(limit=5)
        for queue in pending:
            self.env["shopify.service"].import_customers_from_queue(queue)

    def action_open_related_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Related Customers"),
            "res_model": "res.partner",
            "view_mode": "list,form",
            "domain": [("shopify_customer_id", "!=", False), ("parent_id", "=", False)],
        }

