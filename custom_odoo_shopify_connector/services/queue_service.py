import logging
import time

from odoo import fields

from ..models.license_mixin import license_is_active_strict, trial_batch_limit
from .product_service import ProductService

try:
    from psycopg2.errors import SerializationFailure
except ImportError:  # pragma: no cover
    SerializationFailure = None

_logger = logging.getLogger(__name__)

_SERIALIZATION_RETRIES = 3
_SERIALIZATION_BACKOFF_SEC = 0.2
_FAILURE_MESSAGE_MAX_LEN = 8192


def _is_serialization_failure(exc):
    """True if exc is (or wraps) a PostgreSQL serialization / concurrent-update conflict."""
    stack = [exc]
    seen = set()
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        if SerializationFailure is not None and isinstance(cur, SerializationFailure):
            return True
        if type(cur).__name__ == "SerializationFailure":
            return True
        msg = str(cur)
        if "could not serialize access" in msg:
            return True
        stack.append(getattr(cur, "__cause__", None))
        stack.append(getattr(cur, "__context__", None))
    return False


def _invalidate_env_after_rollback(env):
    """Clear ORM caches after cr.rollback() so subsequent writes use a clean state."""
    inval = getattr(env, "invalidate_all", None)
    if callable(inval):
        inval()
        return
    clear = getattr(env, "clear", None)
    if callable(clear):
        clear()


def _shopify_datetime(dt):
    """Format Odoo Datetime for Shopify API (ISO 8601)."""
    if not dt:
        return None
    return fields.Datetime.to_string(dt).replace(" ", "T") + "Z"


class ProductQueueService:
    """Service layer for Shopify -> Odoo product queue management (batch queue + lines)."""

    def __init__(self, env):
        self.env = env

    def _build_product_params(self, import_based_on, from_date, to_date):
        """Build API params for date filtering; empty means fetch all products."""
        params = {}
        if import_based_on == "create_date":
            min_key, max_key = "created_at_min", "created_at_max"
        else:
            min_key, max_key = "updated_at_min", "updated_at_max"
        if from_date:
            params[min_key] = _shopify_datetime(from_date)
        if to_date:
            params[max_key] = _shopify_datetime(to_date)
        return params

    def enqueue_products_by_date(self, store, import_based_on, from_date, to_date):
        """Fetch products from Shopify; create ONE queue and multiple lines."""
        api_client = store._get_api_client()
        params = self._build_product_params(import_based_on, from_date, to_date)
        products = api_client.get_products(**params)

        Queue = self.env["shopify.product.queue"]
        Line = self.env["shopify.product.queue.line"]

        queue = Queue.create(
            {
                "store_id": store.id,
                "import_based_on": import_based_on or "create_date",
                "from_date": from_date,
                "to_date": to_date,
            }
        )

        line_vals = []
        for product in products:
            vals = self._build_queue_line_vals(queue.id, product)
            if vals:
                line_vals.append(vals)
        if line_vals:
            Line.create(line_vals)

        return queue

    def fetch_products_into_queue(self, queue, from_date=None, to_date=None):
        """Fetch products from Shopify and add them as lines to an existing queue.
        If from_date/to_date are not set, fetches all products.
        """
        queue.ensure_one()
        store = queue.store_id
        api_client = store._get_api_client()
        import_based_on = queue.import_based_on or "create_date"
        from_date = from_date or queue.from_date
        to_date = to_date or queue.to_date
        params = self._build_product_params(import_based_on, from_date, to_date)
        products = api_client.get_products(**params)

        Line = self.env["shopify.product.queue.line"]
        existing_ids = set(queue.line_ids.mapped("shopify_product_id"))
        added = 0
        line_vals = []
        for product in products:
            pid = str(product.get("id") or "")
            if pid and pid not in existing_ids:
                vals = self._build_queue_line_vals(queue.id, product)
                if vals:
                    line_vals.append(vals)
                existing_ids.add(pid)
                added += 1
        if line_vals:
            Line.create(line_vals)

        return added

    def _build_queue_line_vals(self, queue_id, product):
        """Build queue-line values from a Shopify product payload."""
        variants = product.get("variants") or []
        variant = variants[0] if variants else {}
        sku = variant.get("sku") or ""
        price = ProductService(self.env)._to_float(variant.get("price"), default=0.0)
        shopify_product_id = str(product.get("id") or "")
        if not shopify_product_id:
            return False
        return {
            "queue_id": queue_id,
            "shopify_product_id": shopify_product_id,
            "shopify_sku": sku,
            "title": product.get("title") or "",
            "price": price,
            "state": "pending",
        }

    def process_queue(self, queue):
        """Process all pending lines of the given queue."""
        pending_lines = self._claim_pending_lines(queue)
        line_ids = list(pending_lines.ids)
        license_ok = license_is_active_strict(self.env)
        if not license_ok:
            # Trial mode: keep UI usable, but throttle heavy imports.
            line_ids = line_ids[: trial_batch_limit()]
        if not line_ids:
            return
        # Commit claims so a serialization/concurrency abort mid-import does not
        # roll back every line back to pending, and failure handling can run in
        # a fresh transaction.
        self.env.cr.commit()
        _invalidate_env_after_rollback(self.env)

        Line = self.env["shopify.product.queue.line"]
        for line_id in line_ids:
            line = Line.browse(line_id)
            try:
                if not license_ok:
                    time.sleep(1)
                self._process_line_with_retry(line)
                self.env.cr.commit()
                _invalidate_env_after_rollback(self.env)
            except Exception as err:
                self._record_line_failure(line_id, err)
                self.env.cr.commit()
                _invalidate_env_after_rollback(self.env)

    def _claim_pending_lines(self, queue):
        """Atomically claim pending lines for a single queue."""
        self.env.cr.execute(
            """
            WITH picked AS (
                SELECT id
                FROM shopify_product_queue_line
                WHERE queue_id = %s AND state = 'pending'
                ORDER BY id ASC
                FOR UPDATE SKIP LOCKED
            )
            UPDATE shopify_product_queue_line l
            SET state = 'processing'
            FROM picked
            WHERE l.id = picked.id
            RETURNING l.id
            """,
            (queue.id,),
        )
        line_ids = [row[0] for row in self.env.cr.fetchall()]
        return self.env["shopify.product.queue.line"].browse(line_ids)

    def _record_line_failure(self, line_id, error):
        """Persist failed state after a poisoned transaction (e.g. SerializationFailure)."""
        self.env.cr.rollback()
        _invalidate_env_after_rollback(self.env)
        msg = str(error)
        if len(msg) > _FAILURE_MESSAGE_MAX_LEN:
            msg = msg[: _FAILURE_MESSAGE_MAX_LEN - 3] + "..."
        self.env["shopify.product.queue.line"].browse(line_id).write(
            {
                "state": "failed",
                "image_import_state": "failed",
                "message": msg,
            }
        )

    def _process_line_with_retry(self, line):
        """Run import with retries when PostgreSQL reports concurrent-update serialization errors."""
        last_error = None
        for attempt in range(_SERIALIZATION_RETRIES):
            try:
                self._process_line(line)
                return
            except Exception as err:
                last_error = err
                if not _is_serialization_failure(err):
                    raise
                _logger.warning(
                    "Shopify product queue line %s: serialization conflict (attempt %s/%s), retrying",
                    line.id,
                    attempt + 1,
                    _SERIALIZATION_RETRIES,
                )
                self.env.cr.rollback()
                _invalidate_env_after_rollback(self.env)
                line = self.env["shopify.product.queue.line"].browse(line.id)
                time.sleep(_SERIALIZATION_BACKOFF_SEC * (attempt + 1))
        raise last_error

    def _process_line(self, line):
        """Process a single queue line: fetch product from Shopify and import into Odoo."""
        queue = line.queue_id
        store = queue.store_id
        api_client = store._get_api_client()

        product_response = api_client.get_product_by_id(line.shopify_product_id)
        product_payload = product_response.get("product") or product_response

        product_service = ProductService(self.env)
        import_result = product_service.import_shopify_product(product_payload, store) or {}
        image_state = import_result.get("image_import_state") or "skipped"
        image_message = import_result.get("image_import_message") or ""

        message = "Processed successfully."
        if image_message:
            message = "%s Image: %s" % (message, image_message)
        line.write(
            {
                "state": "done",
                "image_import_state": image_state,
                "message": message,
            }
        )

    def process_draft_queues(self):
        """Process all queues that have pending lines (used by cron)."""
        Queue = self.env["shopify.product.queue"]
        queues_with_pending = Queue.search([
            ("line_ids.state", "=", "pending"),
        ], order="create_date asc")
        license_ok = license_is_active_strict(self.env)
        if not license_ok:
            queues_with_pending = queues_with_pending[: trial_batch_limit()]
        for queue in queues_with_pending:
            if not license_ok:
                time.sleep(1)
            self.process_queue(queue)
