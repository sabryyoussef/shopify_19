"""WP-I — Shopify archive / unarchive / cancelled-order reopen (safe policy).

Ambiguity / assumptions (documented before coding):
1. The connector already registers ``orders/updated`` (and ``orders/cancelled``).
   There is **no** dedicated archive/reopen webhook topic in the registration
   plan. Archive, unarchive, and cancelled-order reopen are detected from the
   ``orders/updated`` (and polling UPDATE) order JSON payload.
2. Shopify ``closed_at`` (REST) / closed order = Admin "archive" / close.
   Clearing ``closed_at`` (``orderOpen``) = unarchive. Auto-close after
   fulfillment also sets ``closed_at``; we sync the same metadata flags
   (policy: metadata only, no Odoo workflow change).
3. Shopify Event verb ``re_opened`` usually means **unarchive** (closed→open),
   **not** uncancel. User "Reopen" = a **previously cancelled** Shopify order
   that is active again: detect when Odoo has ``shopify_cancelled`` and the
   payload has a falsy ``cancelled_at``.
4. Local-only Odoo cancels (``state=cancel`` without ``shopify_cancelled``)
   do **not** trigger reopen review.
5. Reopen never resets the SO, never auto-creates a replacement, and never
   reverses accounting/stock. Unarchive only clears archive fields.
"""

from __future__ import annotations

import json
import logging

from odoo import _
from markupsafe import escape as html_escape

from . import lifecycle_logger as llog

_logger = logging.getLogger(__name__)

REOPEN_ACTIVITY_SUMMARY = "Shopify order reopened — manual review required"
REOPEN_PAYLOAD_KEYS = (
    "id",
    "name",
    "cancelled_at",
    "cancel_reason",
    "closed_at",
    "financial_status",
    "fulfillment_status",
    "updated_at",
    "total_price",
)
PAYLOAD_MAX_LEN = 4000


class OrderArchiveReopenService:
    """Apply archive/reopen metadata from a Shopify order payload."""

    def __init__(self, env):
        self.env = env

    def apply_from_payload(self, store, order, payload, correlation_id=None, trace=None):
        if not store or not order or not payload:
            return False
        changed = False
        if getattr(store, "sync_archive_status", True):
            if self._sync_archive(store, order, payload, correlation_id=correlation_id, trace=trace):
                changed = True
        if getattr(store, "sync_reopen_manual_review", True):
            if self._sync_reopen(store, order, payload, correlation_id=correlation_id, trace=trace):
                changed = True
        return changed

    # ------------------------------------------------------------------
    # Archive / unarchive (closed_at)
    # ------------------------------------------------------------------
    def _sync_archive(self, store, order, payload, correlation_id=None, trace=None):
        closed_at_raw = payload.get("closed_at")
        is_archived = bool(closed_at_raw)
        was_archived = bool(order.shopify_is_archived)
        if is_archived == was_archived:
            # Keep timestamp fresh only when newly archived; noop otherwise.
            if is_archived and not order.shopify_archived_at:
                order.write({"shopify_archived_at": self._parse_dt(closed_at_raw)})
            return False

        if is_archived:
            vals = {
                "shopify_is_archived": True,
                "shopify_archived_at": self._parse_dt(closed_at_raw) or self._now(),
            }
            order.write(vals)
            order.message_post(
                body=_(
                    "Shopify order archived (closed_at=%(closed)s). "
                    "Odoo Sales Order workflow state was not changed."
                )
                % {"closed": html_escape(str(closed_at_raw))}
            )
            self._log(
                store,
                _("Shopify archive flag set on %s") % order.name,
                {"closed_at": closed_at_raw, "odoo_state": order.state},
                order_id=order.shopify_order_id,
                correlation_id=correlation_id,
            )
            if trace:
                trace.step(
                    llog.STEP_ARCHIVE_SYNCED,
                    so=order.name,
                    status="archived",
                    msg="closed_at set",
                )
        else:
            order.write(
                {
                    "shopify_is_archived": False,
                    "shopify_archived_at": False,
                }
            )
            order.message_post(
                body=_(
                    "Shopify order unarchived (closed_at cleared). "
                    "Odoo Sales Order workflow state was not changed."
                )
            )
            self._log(
                store,
                _("Shopify archive flag cleared on %s") % order.name,
                {"odoo_state": order.state},
                order_id=order.shopify_order_id,
                correlation_id=correlation_id,
            )
            if trace:
                trace.step(
                    llog.STEP_ARCHIVE_SYNCED,
                    so=order.name,
                    status="unarchived",
                    msg="closed_at cleared",
                )
        return True

    # ------------------------------------------------------------------
    # Cancelled-order reopen → manual review only
    # ------------------------------------------------------------------
    def _sync_reopen(self, store, order, payload, correlation_id=None, trace=None):
        # Automatic workflow changes are intentionally never enabled.
        if not order.shopify_cancelled:
            return False
        if payload.get("cancelled_at"):
            return False

        # Already pending: idempotent — no duplicate chatter/activity.
        if order.shopify_reopen_pending_review:
            return False

        payload_ref = self._safe_payload_ref(payload)
        order.write(
            {
                "shopify_reopen_pending_review": True,
                "shopify_reopened_at": self._now(),
                "shopify_reopen_payload": payload_ref,
            }
        )
        order.message_post(
            body=_(
                "<p><b>Shopify order reopened — manual review required.</b></p>"
                "<p>The Shopify order is no longer cancelled, but the Odoo Sales "
                "Order state was <b>not</b> changed automatically. Do not assume "
                "invoices, payments, deliveries, refunds, or stock were reversed.</p>"
                "<p>Use <i>Mark Reopen Reviewed</i> after review, or "
                "<i>Create Replacement Quotation</i> if a new draft is needed.</p>"
            )
        )
        self._ensure_reopen_activity(store, order)
        self._log(
            store,
            _("Shopify reopen pending review on %s") % order.name,
            {
                "odoo_state": order.state,
                "shopify_cancelled": True,
                "cancelled_at": payload.get("cancelled_at"),
            },
            order_id=order.shopify_order_id,
            correlation_id=correlation_id,
        )
        if trace:
            trace.step(
                llog.STEP_REOPEN_REVIEW_FLAGGED,
                so=order.name,
                status="pending_review",
                msg="cancelled_at cleared; SO state unchanged",
            )
        return True

    def _ensure_reopen_activity(self, store, order):
        Activity = self.env["mail.activity"].sudo()
        existing = Activity.search(
            [
                ("res_model", "=", "sale.order"),
                ("res_id", "=", order.id),
                ("summary", "=", REOPEN_ACTIVITY_SUMMARY),
            ],
            limit=1,
        )
        if existing:
            return existing

        user = False
        if store.reopen_activity_user_id:
            user = store.reopen_activity_user_id
        elif order.user_id:
            user = order.user_id
        else:
            user = store._resolve_import_order_salesperson_user()
        if not user:
            user = self.env.user

        try:
            return order.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=REOPEN_ACTIVITY_SUMMARY,
                note=_(
                    "Shopify cancelled order was reopened. Review the Odoo Sales "
                    "Order manually. State was not auto-reset."
                ),
            )
        except Exception as exc:
            _logger.warning(
                "Could not schedule reopen activity on SO %s: %s", order.name, exc
            )
            return False

    def _safe_payload_ref(self, payload):
        data = {k: payload.get(k) for k in REOPEN_PAYLOAD_KEYS}
        try:
            raw = json.dumps(data, default=str, sort_keys=True)
        except Exception:
            raw = str(data)
        if len(raw) > PAYLOAD_MAX_LEN:
            raw = raw[: PAYLOAD_MAX_LEN - 3] + "..."
        return raw

    def _parse_dt(self, value):
        if not value:
            return False
        from odoo import fields as odoo_fields

        try:
            return odoo_fields.Datetime.to_datetime(value)
        except Exception:
            try:
                # Shopify ISO with timezone: keep naive wall-clock portion.
                cleaned = str(value).replace("T", " ")[:19]
                return odoo_fields.Datetime.to_datetime(cleaned)
            except Exception:
                return self._now()

    def _now(self):
        from odoo import fields as odoo_fields

        return odoo_fields.Datetime.now()

    def _log(self, store, message, payload, status="success", order_id=False, correlation_id=None):
        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message=message,
            payload=payload,
            status=status,
            order_id=order_id,
            correlation_id=correlation_id,
        )
