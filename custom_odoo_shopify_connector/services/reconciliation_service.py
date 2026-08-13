"""P3 — Polling fallback / reconciliation.

Webhooks are the primary realtime path; polling is a *fallback reconciliation*
mechanism for missed or delayed Shopify events. This service never re-implements
CREATE/UPDATE/FULFILLMENT business logic: it only *detects* what changed for a
polled order and enqueues the right lifecycle operation so the existing queue
router (P2/P4) and services perform the actual work.

Design guarantees:
  * Distinct created vs updated checkpoints (not a single created_at).
  * Small overlap window (default 10 min) + idempotency to avoid boundary misses.
  * Cursor (page_info) pagination: every page fetched; checkpoint advances only
    after all pages of a scan succeed.
  * New-order import filters (should_import_order) gate CREATE only; they never
    block reconciliation of already-imported orders.
  * Operation-aware queue dedup: an UPDATE job never blocks a FULFILLMENT job.
  * Payment safety unchanged: polling never registers a payment; it only routes
    operations to the P5/P6-gated services.
"""
import json
import logging
from datetime import timedelta

from odoo import fields

from . import lifecycle_logger as llog
from .order_import_service import OrderImportService

_logger = logging.getLogger(__name__)

_MAX_PAGES = 1000
_DEFAULT_OVERLAP_MIN = 10


def _parse_shopify_dt(value):
    """Parse a Shopify ISO8601 timestamp to a naive UTC datetime (or None)."""
    if not value:
        return None
    try:
        from dateutil import parser as _dtparser

        dt = _dtparser.parse(str(value))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = _to_utc_naive(dt)
    return dt


def _to_utc_naive(dt):
    import datetime as _dt

    return dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)


class ShopifyReconciliationService:
    # Job types recognised by the queue router.
    OP_CREATE = llog.OP_CREATE
    OP_UPDATE = llog.OP_UPDATE
    OP_FULFILLMENT = llog.OP_FULFILLMENT

    def __init__(self, env):
        self.env = env
        self.import_service = OrderImportService(env)

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    @staticmethod
    def _next_page_info(headers):
        """Extract the ``page_info`` cursor for rel="next" from a Link header."""
        if not headers:
            return None
        link = headers.get("Link") or headers.get("link")
        if not link:
            return None
        for part in link.split(","):
            if 'rel="next"' not in part:
                continue
            start = part.find("<")
            end = part.find(">", start + 1)
            if start == -1 or end == -1:
                continue
            url = part[start + 1:end]
            # page_info=<cursor>
            for chunk in url.split("?", 1)[-1].split("&"):
                if chunk.startswith("page_info="):
                    return chunk.split("=", 1)[1]
        return None

    def _iter_pages(self, client, trace, **params):
        """Yield successive pages of Shopify orders following the Link cursor.

        Works with a full client (sets ``last_response_headers``) and with simple
        test clients that only implement ``get_orders`` (single page)."""
        page = 0
        next_params = dict(params)
        while True:
            orders = client.get_orders(**next_params)
            page += 1
            trace.step(
                llog.STEP_POLLING_PAGE_FETCHED,
                status="ok",
                msg="page=%s count=%s" % (page, len(orders or [])),
            )
            yield orders or []
            headers = getattr(client, "last_response_headers", {}) or {}
            page_info = self._next_page_info(headers)
            if not page_info or page >= _MAX_PAGES:
                break
            next_params = {"limit": params.get("limit", 250), "page_info": page_info}

    # ------------------------------------------------------------------
    # Lookups / dedup
    # ------------------------------------------------------------------
    def _find_order(self, store, shopify_order_id):
        if not shopify_order_id:
            return self.env["sale.order"].browse()
        return self.env["sale.order"].search(
            [
                ("shopify_order_id", "=", str(shopify_order_id)),
                ("shopify_instance_id", "=", store.id),
            ],
            limit=1,
        )

    def _pending_queue(self, store, shopify_order_id, job_types):
        return self.env["shopify.order.queue"].search(
            [
                ("store_id", "=", store.id),
                ("shopify_order_id", "=", str(shopify_order_id)),
                ("job_type", "in", list(job_types)),
                ("state", "in", ["pending", "processing"]),
            ],
            limit=1,
        )

    # ------------------------------------------------------------------
    # Operation detection
    # ------------------------------------------------------------------
    def detect_operations(self, store, order, allow_create=True):
        """Return (existing_order, [(op, reason), ...]) for a polled order.

        Existing-order reconciliation is NOT gated by should_import_order; only a
        brand-new CREATE is."""
        oid = str(order.get("id") or "")
        existing = self._find_order(store, oid)
        ops = []

        if not existing:
            if allow_create and self.import_service.should_import_order(store, order):
                ops.append((self.OP_CREATE, "new_order"))
            return existing, ops

        # UPDATE detection via updated_at vs stored baseline.
        polled_updated = order.get("updated_at")
        baseline = existing.shopify_updated_at
        if polled_updated:
            if not baseline:
                # Bootstrap baseline once; do not raise a blind UPDATE for a
                # legacy order that predates this field.
                existing.shopify_updated_at = str(polled_updated)
            else:
                p = _parse_shopify_dt(polled_updated)
                b = _parse_shopify_dt(baseline)
                if p and b and p > b:
                    ops.append((self.OP_UPDATE, "updated_at_changed"))

        # FULFILLMENT detection via new fulfillment ids / status.
        fstatus = (order.get("fulfillment_status") or "").strip().lower()
        fids = [str(f.get("id")) for f in (order.get("fulfillments") or []) if f.get("id")]
        processed = set(filter(None, (existing.shopify_fulfillment_ids or "").split(",")))
        new_fids = [f for f in fids if f not in processed]
        if fstatus in ("fulfilled", "partial"):
            if new_fids:
                ops.append((self.OP_FULFILLMENT, "new_fulfillment_ids=%s" % ",".join(new_fids)))
            elif not fids and not self.import_service._delivery_completed(existing):
                ops.append((self.OP_FULFILLMENT, "status=%s (no ids, delivery pending)" % fstatus))

        return existing, ops

    # ------------------------------------------------------------------
    # Enqueue (operation-aware dedup)
    # ------------------------------------------------------------------
    def _enqueue(self, store, order, op, reason, trace, dry_run=False):
        oid = str(order.get("id") or "")
        Queue = self.env["shopify.order.queue"]

        # Dedup: create also collides with legacy 'order' auto jobs.
        if op == self.OP_CREATE:
            if self._find_order(store, oid):
                trace.step(llog.STEP_POLLING_QUEUE_DEDUP, op=op, shop_order=oid,
                           status="dedup", msg="sale order already exists")
                return None
            dup = self._pending_queue(store, oid, (self.OP_CREATE, llog.OP_ORDER))
        else:
            dup = self._pending_queue(store, oid, (op,))
        if dup:
            trace.step(llog.STEP_POLLING_QUEUE_DEDUP, op=op, shop_order=oid, queue=dup.id,
                       status="dedup", msg="pending %s job already queued" % op)
            return None

        step = {
            self.OP_CREATE: llog.STEP_POLLING_CREATE_DETECTED,
            self.OP_UPDATE: llog.STEP_POLLING_UPDATE_DETECTED,
            self.OP_FULFILLMENT: llog.STEP_POLLING_FULFILLMENT_DETECTED,
        }.get(op, llog.STEP_POLLING_UPDATE_DETECTED)

        if dry_run:
            trace.step(step, op=op, shop_order=oid, status="dry_run", msg=reason)
            return None

        queue = Queue.create(
            {
                "store_id": store.id,
                "shopify_order_id": oid or False,
                "payload": json.dumps(order),
                "state": "pending",
                "job_type": op,
                "correlation_id": trace.correlation_id,
            }
        )
        trace.step(step, op=op, shop_order=oid, queue=queue.id, status="ok", msg=reason)
        return queue

    def reconcile_order(self, store, order, trace, allow_create=True, dry_run=False):
        existing, ops = self.detect_operations(store, order, allow_create=allow_create)
        oid = str(order.get("id") or "")
        if not ops:
            if existing:
                trace.step(llog.STEP_POLLING_SKIPPED_NO_CHANGE, shop_order=oid,
                           so=existing.name, status="ok", msg="no material change")
            else:
                trace.step(llog.STEP_POLLING_SKIPPED_NO_CHANGE, shop_order=oid,
                           status="ok", msg="new order filtered out by import policy")
            return []
        enqueued = []
        for op, reason in ops:
            if self._enqueue(store, order, op, reason, trace, dry_run=dry_run):
                enqueued.append(op)
        return enqueued

    # ------------------------------------------------------------------
    # Scans
    # ------------------------------------------------------------------
    def _overlap(self, store):
        minutes = getattr(store, "poll_overlap_minutes", None)
        if minutes is None:
            minutes = _DEFAULT_OVERLAP_MIN
        return timedelta(minutes=max(0, int(minutes or 0)))

    def _scan(self, store, checkpoint_field, filter_key, ts_key, allow_create, correlation_id=None):
        """Shared created/updated scan.

        - checkpoint_field: store field holding the checkpoint datetime.
        - filter_key: Shopify query filter (created_at_min / updated_at_min).
        - ts_key: order timestamp key used to advance the checkpoint.
        """
        from .queue_service import _shopify_datetime

        trace = llog.LifecycleTrace(
            correlation_id=correlation_id or llog.new_correlation_id(),
            op="poll",
        )
        client = store._get_api_client_for_scheduler(log_type="order")
        if not client:
            return {"pages": 0, "enqueued": 0, "advanced": False}

        prev_checkpoint = getattr(store, checkpoint_field, False)
        params = {"limit": 250}
        # updated scan needs status=any so fulfilled/closed orders are returned.
        if filter_key == "updated_at_min":
            params["status"] = "any"
        base = prev_checkpoint or store.order_import_start_date
        if base:
            since = fields.Datetime.to_datetime(base) - self._overlap(store)
            params[filter_key] = _shopify_datetime(since)

        trace.step(
            llog.STEP_POLLING_STARTED,
            status="ok",
            msg="scan=%s prev_checkpoint=%s overlap_min=%s filter=%s"
            % (
                checkpoint_field,
                fields.Datetime.to_string(prev_checkpoint) if prev_checkpoint else "none",
                int(self._overlap(store).total_seconds() // 60),
                params.get(filter_key, "none"),
            ),
        )

        max_ts = None
        enqueued_total = 0
        try:
            for orders in self._iter_pages(client, trace, **params):
                for order in orders:
                    enqueued_total += len(
                        self.reconcile_order(store, order, trace, allow_create=allow_create)
                    )
                    ts = _parse_shopify_dt(order.get(ts_key))
                    if ts and (max_ts is None or ts > max_ts):
                        max_ts = ts
        except Exception as exc:
            _logger.exception("Polling scan failed (store=%s field=%s)", store.id, checkpoint_field)
            trace.step(
                llog.STEP_POLLING_FAILED,
                status="failed",
                level=logging.ERROR,
                msg="scan=%s error=%s (checkpoint NOT advanced)" % (checkpoint_field, exc),
            )
            self.env["shopify.sync.log.mixin"].create_log(
                store=store, log_type="order", message=str(exc), payload=False,
                status="failed", correlation_id=trace.correlation_id,
            )
            return {"pages": 0, "enqueued": enqueued_total, "advanced": False}

        # Advance checkpoint only after all pages processed successfully.
        advanced = False
        if max_ts is not None:
            store.write({checkpoint_field: max_ts})
            advanced = True
            trace.step(
                llog.STEP_POLLING_CHECKPOINT_ADVANCED,
                status="ok",
                msg="scan=%s previous=%s new=%s enqueued=%s"
                % (
                    checkpoint_field,
                    fields.Datetime.to_string(prev_checkpoint) if prev_checkpoint else "none",
                    fields.Datetime.to_string(max_ts),
                    enqueued_total,
                ),
            )
        return {"pages": 0, "enqueued": enqueued_total, "advanced": advanced,
                "correlation_id": trace.correlation_id}

    def scan_created_orders(self, store, correlation_id=None):
        """New-order discovery by creation timestamp -> CREATE (idempotent)."""
        return self._scan(
            store,
            checkpoint_field="last_order_import_time",
            filter_key="created_at_min",
            ts_key="created_at",
            allow_create=True,
            correlation_id=correlation_id,
        )

    def scan_updated_orders(self, store, correlation_id=None):
        """Changed-order reconciliation by update timestamp -> UPDATE/FULFILLMENT."""
        return self._scan(
            store,
            checkpoint_field="last_order_update_time",
            filter_key="updated_at_min",
            ts_key="updated_at",
            allow_create=False,
            correlation_id=correlation_id,
        )

    def run_poll(self, store, correlation_id=None):
        created = self.scan_created_orders(store, correlation_id=correlation_id)
        updated = self.scan_updated_orders(store, correlation_id=correlation_id)
        return {"created": created, "updated": updated}

    # ------------------------------------------------------------------
    # Controlled manual backfill / reconciliation
    # ------------------------------------------------------------------
    def backfill(self, store, date_from, date_to, dry_run=True, limit=200):
        """Controlled, bounded reconciliation for an explicit date range.

        Routes through the same detection + queue router; never mutates documents
        directly. Does not advance the polling checkpoints. Historical mass
        remediation remains out of scope (P10)."""
        from .queue_service import _shopify_datetime

        trace = llog.LifecycleTrace(correlation_id=llog.new_correlation_id(), op="poll")
        client = store._get_api_client_for_scheduler(log_type="order")
        if not client:
            return {"scanned": 0, "enqueued": 0, "dry_run": dry_run}

        params = {"limit": min(250, max(1, int(limit or 200))), "status": "any"}
        if date_from:
            params["updated_at_min"] = _shopify_datetime(fields.Datetime.to_datetime(date_from))
        if date_to:
            params["updated_at_max"] = _shopify_datetime(fields.Datetime.to_datetime(date_to))

        trace.step(
            llog.STEP_POLLING_STARTED,
            status="ok",
            msg="backfill from=%s to=%s dry_run=%s limit=%s" % (date_from, date_to, dry_run, limit),
        )

        scanned = 0
        enqueued = 0
        for orders in self._iter_pages(client, trace, **params):
            for order in orders:
                if scanned >= int(limit or 200):
                    break
                scanned += 1
                # Backfill may create missing orders and reconcile existing ones.
                enqueued += len(
                    self.reconcile_order(store, order, trace, allow_create=True, dry_run=dry_run)
                )
            if scanned >= int(limit or 200):
                break

        return {"scanned": scanned, "enqueued": enqueued, "dry_run": dry_run,
                "correlation_id": trace.correlation_id}
