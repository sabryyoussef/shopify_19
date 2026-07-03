# WP70 — Post-UAT Remediation Plan

**Client:** iZone A-Zone (WorldPosta)  
**Source:** Chatwoot #46 (2026-06-29)  
**Module:** `custom_odoo_shopify_connector`  
**Target version:** 19.0.1.0.5  
**Date:** 2026-07-02

---

## Scope

Close gaps identified after WP69 UAT sign-off. WP69 delivered core connector features (fees, refunds, exchange scaffold, webhooks, logs). This plan addresses **post-UAT client feedback** not fully covered.

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| G6 | Enforce `auto_create_product_if_not_found` | P0 | Implemented |
| G1 | Partial payment registration | P0 | Implemented |
| G2 | Cancel after invoice → credit note | P0 | Implemented |
| G4 | Exchange price difference | P1 | Implemented |
| G5 | Stock return on refund | P1 | Implemented |
| G3 | Shopify order edit sync | P1 | Implemented |
| G7 | Automated UAT tests | P2 | Implemented |
| G8 | User manual screenshots | P2 | Pending (requires live UI) |

---

## Phase 0 — Quick fixes

### G6 — Product auto-create flag

**Files:** `services/order_service.py`

- When `auto_create_product_if_not_found = False` and product not mapped → `ValidationError`, order import fails with clear SKU/variant message.

### G8 — Screenshots

- Capture from `odoo_test` via Playwright e2e (`/opt/odoo-log-report-2026-06-28/e2e/`).
- Place in `docs/user-guide/images/`.

---

## Phase 1 — Accounting

### G1 — Partial payment

**Files:** `services/payment_fee_service.py`, `services/order_import_service.py`, `models/sale_order.py`, `models/account_payment.py`

- Extract paid amount from Shopify `transactions` (successful `sale`).
- Register `min(paid - already_registered, invoice_residual)`.
- Track `shopify_amount_paid` on sale order.
- Idempotency via `shopify_transaction_id` on `account.payment`.

**Defaults:** Confirm SO on `partially_paid`; register partial amount only.

### G2 — Cancel → credit note

**Files:** `services/refund_sync_service.py`, `controllers/shopify_webhook.py`, `models/shopify_service.py`, `models/shopify_store.py`

- Store config: `cancel_sync_mode` = `credit_note` (default) | `cancel_only`.
- Before cancel: if posted invoice exists → full reversal CN (dedup `cancel-{order_id}`).
- Then cancel SO.

### G4 — Exchange pricing

**Files:** `models/shopify_exchange_wizard.py`, `views/shopify_exchange_wizard.xml`, `models/sale_order.py`

- Wizard shows `replacement_price_unit` (default: product list price).
- Credit at original line price; replacement at chosen price.
- Price diff: extra CN line if cheaper; balance line on replacement SO if more expensive.

---

## Phase 2 — Returns & stock

### G5 — Refund restock

**Files:** `services/return_picking_service.py`, `services/refund_sync_service.py`, `models/shopify_store.py`

- Store config: `refund_restock_mode` = `credit_note_only` (default) | `odoo_restock` | `both`.
- Parse Shopify `restock_type` from refund payload.
- Create incoming return picking when `return` and mode allows.
- Odoo → Shopify refunds: configurable `restock_type` (default `no_restock`).

---

## Phase 3 — Order lifecycle

### G3 — Order edit sync

**Files:** `services/order_update_service.py`, `services/order_service.py`, `services/order_import_service.py`, `models/shopify_store.py`

- Store config: `order_edit_sync_mode`:
  - `ignore` — current behavior
  - `draft_sent` — update lines on draft/sent SO (default)
  - `with_adjustments` — also qty reductions on invoiced SO via partial CN
- Match lines by `shopify_line_item_id`.
- Re-apply fees after line sync.

---

## Phase 4 — Quality

### G7 — Tests

| Test file | Scenarios |
|-----------|-----------|
| `test_auto_create_product.py` | Flag on/off |
| `test_partial_payment.py` | Partially paid amount |
| `test_cancel_credit_note.py` | Cancel after invoice |
| `test_exchange_wizard.py` | Price diff |
| `test_refund_restock.py` | Restock picking |
| `test_order_update_sync.py` | Line qty update |

---

## Configuration (iZone defaults)

| Setting | Recommended value |
|---------|-------------------|
| `auto_create_product_if_not_found` | False (use Product Mapping) |
| `cancel_sync_mode` | `credit_note` |
| `order_edit_sync_mode` | `draft_sent` |
| `refund_restock_mode` | `odoo_restock` (after location mapping) |
| `partial_payment_mode` | `register_paid_amount` |

---

## UAT sign-off checklist

- [ ] No discount / coupon / auto discount orders
- [ ] Multi-product, multi-shipment, free shipping
- [ ] Partial payment → partial Odoo payment
- [ ] Partial / full refund → CN (+ restock if enabled)
- [ ] Exchange with cheaper / more expensive product
- [ ] Cancel before / after invoice
- [ ] Order edit on draft SO
- [ ] Duplicate webhook → no duplicate SO/CN/payment
- [ ] Failed queue retry → same order resumed
- [ ] Missing product with flag off → clear error
- [ ] Sync Logs visible

---

## OpenProject

Create child WPs under **#69** for G1–G7. Link to this document.

*Implementation branch: WP70 remediation — module 19.0.1.0.5*
