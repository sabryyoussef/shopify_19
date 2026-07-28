# WP-I UAT Evidence — Archive / Reopen (safe policy)

**Date:** 2026-07-28  
**Environment:** Odoo `https://test.drpaws.ai` · DB `pet_spot_elsahel_test` · Shopify Pet Spot `ucbah1-5e.myshopify.com`  
**Module:** `custom_odoo_shopify_connector` **19.0.1.9.0**  
**Scope:** PetSpot Test only — **not** deployed to iZone Production / A-Zone.

## Policy (approved)

| Event | Behaviour |
|-------|-----------|
| **Archive** | Metadata only (`shopify_is_archived`, `shopify_archived_at`). No SO state / invoice / payment / delivery / refund / stock changes. |
| **Unarchive** | Clears archive flags only. |
| **Reopen (cancelled→active)** | Manual review only: pending flag, chatter, activity. Never auto-reset SO. Manual: Mark Reviewed / Create Replacement Quotation. |

## Webhook / topic handling

| Signal | Shopify source | Detection |
|--------|----------------|-----------|
| Archive | `orders/updated` (+ polling UPDATE) | `closed_at` truthy |
| Unarchive | same | `closed_at` falsy after archived |
| Cancelled-order reopen | same | Odoo `shopify_cancelled` and payload `cancelled_at` falsy |

**Ambiguity documented in code** (`services/order_archive_reopen_service.py`):

1. No dedicated archive/reopen webhook topics are registered; connector already uses `orders/updated`.
2. Shopify Event `re_opened` usually means **unarchive** (closed→open), not uncancel.
3. Auto-close after fulfillment also sets `closed_at` — synced as archive metadata (same safe policy).
4. Local Odoo cancel without `shopify_cancelled` does **not** trigger reopen review.

Pipeline reuse: `shopify.order.queue` UPDATE path → `OrderArchiveReopenService.apply_from_payload` **before** `OrderUpdateService` (so cancelled SO still gets flags).

## Schema changes (`sale.order` / `shopify.store`)

**sale.order**

- `shopify_is_archived` (Boolean, index)
- `shopify_archived_at` (Datetime)
- `shopify_reopen_pending_review` (Boolean, index)
- `shopify_reopened_at` (Datetime)
- `shopify_reopen_payload` (Text)
- `shopify_reopen_replacement_id` (Many2one → sale.order)
- `shopify_reopen_original_id` (Many2one → sale.order)

**shopify.store** (defaults)

- `sync_archive_status` = True
- `sync_reopen_manual_review` = True
- `reopen_activity_user_id` (optional)
- Auto workflow reset: **always disabled** (no enable switch)

## Automated tests

```bash
CONF=/home/sabry/odoo_base/base_odoo_19/config/projects/pet_spot_elsahel_test.conf
PY=/home/sabry/odoo_base/base_odoo_19/venv19/bin/python3
BIN=/home/sabry/odoo_base/base_odoo_19/odoo19/odoo19/odoo-bin
systemctl --user stop pet_spot_elsahel_test.service
$PY $BIN -c $CONF -d pet_spot_elsahel_test \
  -u custom_odoo_shopify_connector \
  --test-enable --stop-after-init \
  --test-tags=shopify_wp_i,shopify_wp_ab,shopify_wp_p1p2
systemctl --user start pet_spot_elsahel_test.service
```

**Result:** `0 failed, 0 error(s) of 37 tests`  
Tags: `shopify_wp_i` (13) + `shopify_wp_ab` (5) + `shopify_wp_p1p2` (19)  
Log: `/tmp/wp_i_test5.log`

## Manual UAT (PetSpot Test) — Odoo record IDs

| Case | Result | Records |
|------|--------|---------|
| Archive | PASS — flag set, state unchanged (`sale`) | SO **S00357** id **430** |
| Unarchive | PASS — flag cleared, state unchanged | same |
| Reopen | PASS — stays `cancel`, pending review, 1 chatter, 1 activity | SO **S00358** id **431**, activity **43** |
| Replacement quotation | PASS — draft linked, duplicate blocked | **S00359** id **432** |
| Mark reviewed | PASS — clears pending only | **431** |

JSON: `/home/sabry/iZone_UAT_Proof_20260728/wp_i/uat_results.json` (`all_ok: true`)

Store: Pet Spot id **1** · `sync_archive_status=True` · `sync_reopen_manual_review=True`

## Screenshots

`/home/sabry/iZone_UAT_Proof_20260728/wp_i/screenshots/`

| File | Shows |
|------|--------|
| `01_archived_flag.png` | Archived banner + chatter (state still Sales Order) |
| `01b_archived_fields.png` | Shopify tab Archive & Reopen section |
| `02_reopen_warning_and_activity.png` | Warning banner, Mark Reviewed, activity, cancelled state |
| `02b_reopen_fields.png` | Reopen fields on Shopify tab |
| `03_replacement_quotation.png` | Linked draft **S00359** |
| `04_archived_search_filter.png` / `04b_filters_menu.png` | Filters: Shopify Archived / Active / Reopen Pending |

## Before → after

| | Before (19.0.1.8.0) | After (19.0.1.9.0) |
|--|---------------------|-------------------|
| Archive | No handling | Metadata + UI + filters; SO untouched |
| Reopen | Gap | Pending review + activity + manual actions only |
| Auto reset cancelled SO | N/A | **Never** |

## Rollback

1. Downgrade / restore module to `19.0.1.8.0` and `-u custom_odoo_shopify_connector`.
2. Or disable store flags: `sync_archive_status=False`, `sync_reopen_manual_review=False` (stops new side effects; columns remain).
3. Optional: clear flags on affected SOs if needed for UI cleanliness.

## Recommendation — iZone A-Zone

**Ready for review / staged deploy after explicit approval.**  
Safe defaults match the approved policy. Do **not** enable any auto-reset of cancelled orders.  
Deploy path: PetSpot Test (done) → approve → iZone A-Zone only.

### Evidence split

| Type | Status |
|------|--------|
| Automated technical tests (`shopify_wp_*`) | **37/37 pass** |
| Manual UAT on PetSpot Test (JSON + screenshots) | **PASS** (`all_ok`) |
| Live Shopify Admin archive/reopen click | Not required for this validation (payload-driven service + UPDATE pipeline proven) |
