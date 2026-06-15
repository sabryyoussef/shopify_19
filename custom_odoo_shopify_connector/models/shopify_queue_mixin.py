from odoo import api, fields, models, _


class ShopifyQueueMixin(models.AbstractModel):
    _name = "shopify.queue.mixin"
    _description = "Shopify Queue Mixin"

    # Model configuration (override per queue model as needed)
    _queue_state_field = "status"  # or "state"
    _queue_failed_value = "failed"
    _queue_pending_value = "pending"
    _queue_next_retry_field = "next_retry_at"
    _queue_retry_count_field = "retry_count"
    _queue_last_error_field = "last_error"
    _queue_error_message_field = "error_message"
    _queue_processed_records_field = "processed_records"
    _queue_log_operation = "other"  # shopify.sync.log.operation

    def _queue_write(self, values):
        """Internal helper to write values without hard-coding field names."""
        return self.write(values)

    def action_retry_failed(self):
        state_field = self._queue_state_field
        next_retry_field = self._queue_next_retry_field
        failed_value = self._queue_failed_value
        pending_value = self._queue_pending_value

        failed = self.filtered(lambda q: getattr(q, state_field) == failed_value)
        if failed:
            failed._queue_write(
                {
                    state_field: pending_value,
                    next_retry_field: False,
                }
            )
        return True

    def action_reset_to_pending(self):
        state_field = self._queue_state_field
        next_retry_field = self._queue_next_retry_field
        retry_count_field = self._queue_retry_count_field
        last_error_field = self._queue_last_error_field
        error_message_field = self._queue_error_message_field
        processed_field = self._queue_processed_records_field

        values = {
            state_field: self._queue_pending_value,
            next_retry_field: False,
        }

        if retry_count_field in self._fields:
            values[retry_count_field] = 0
        if last_error_field in self._fields:
            values[last_error_field] = False
        if error_message_field in self._fields:
            values[error_message_field] = False
        if processed_field in self._fields:
            values[processed_field] = 0

        self._queue_write(values)
        return True

    def action_open_logs(self):
        self.ensure_one()
        operation = self._queue_log_operation or "other"
        return {
            "type": "ir.actions.act_window",
            "name": _("Queue Sync Log"),
            "res_model": "shopify.sync.log",
            "view_mode": "list,form",
            "domain": [
                ("store_id", "=", self.store_id.id),
                ("operation", "=", operation),
                ("date", ">=", self.create_date),
            ],
            "context": {"search_default_failed": 1},
        }

