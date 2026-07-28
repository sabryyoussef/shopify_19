# Changelog — custom_odoo_shopify_connector

## 19.0.1.9.0 — 2026-07-28

### WP-I — Shopify Archive / Reopen (safe policy)

**Archive (metadata only)**
- Sync Shopify closed/archived status from `orders/updated` / polling UPDATE via `closed_at`.
- Fields: `shopify_is_archived`, `shopify_archived_at`.
- Form banner + search filters (Archived / Not Archived).
- Idempotent; chatter only when the flag changes.
- Unarchive clears flags only — never changes SO workflow.

**Reopen (manual review only)**
- Detect cancelled→active on UPDATE when Odoo has `shopify_cancelled` and payload `cancelled_at` is cleared.
- Fields: `shopify_reopen_pending_review`, `shopify_reopened_at`, `shopify_reopen_payload`.
- Warning banner, one chatter note, one activity (idempotent on replay).
- Manual actions: **Mark Reopen Reviewed**, **Create Replacement Quotation** (confirm dialog; one linked draft; blocks duplicates).
- Never auto-resets cancelled SO; never auto-creates replacement; never reverses accounting/stock.

**Store settings (safe defaults)**
- `sync_archive_status` = True
- `sync_reopen_manual_review` = True
- `reopen_activity_user_id` optional
- Automatic reopen workflow changes: always disabled (no setting)

**Ambiguity documented in** `services/order_archive_reopen_service.py`.

### Tests
- Tag `shopify_wp_i` — archive/reopen/idempotency/manual actions/queue path.

## 19.0.1.8.0 — 2026-07-28

P1/P2: return after cancel, address, inbound tracking, damaged no-restock, notes/currency, tags, shipping/payment method update.

## 19.0.1.7.0 — 2026-07-28

P0: payment sync on update (WP-A), stock gate `confirm_require_stock` (WP-B).
