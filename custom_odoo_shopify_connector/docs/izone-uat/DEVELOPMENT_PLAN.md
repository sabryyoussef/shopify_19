# Development plan — gaps from scenario code analysis

**Date:** 2026-07-28  
**Based on:** `/home/sabry/iZone_Shopify_Odoo_Code_Analysis.md`  
**Scenarios:** `/home/sabry/iZone_Shopify_Odoo_Testing_Scenarios.md`  
**Module:** `custom_odoo_shopify_connector`  
**OpenProject:** [#412](https://master.tailcf9988.ts.net:10081/work_packages/412) under [#409](https://master.tailcf9988.ts.net:10081/work_packages/409)  
**Env for build/UAT:** PetSpot Test first → then iZone A-Zone parity

---

## 1. Do we need development?

**Yes.** Core import/cancel/refund/fulfillment is strong, but client scenarios need:

| Priority | Theme | Why |
|----------|-------|-----|
| **P0** | Payment updates on `orders/updated` | Blocks SO-03, SO-04 follow-on, BC-07 “rest on delivery” automation |
| **P0** | Stock gate before confirm | Explicit client rule BC-02 |
| **P1** | Cancel/refund after done delivery | SO-06, BC-04, BC-08 |
| **P1** | Address + inbound tracking | SO-14, SO-20 |
| **P1** | Damaged replacement policy | BC-05 |
| **P2** | Notes, currency, tags, reopen, archive | SO-23–27 |
| **P2** | Shipping method / payment method change | SO-15, SO-16 |
| **Skip** | Live BOSTA / InstaPay settlement cron | N/A under simulation rule |

---

## 2. Work packages (proposed)

### WP-A — Payment sync on order update (P0)

**Scenarios:** SO-03, SO-04, BC-07  
**Problem:** UPDATE queue never re-runs `apply_workflow`, so pending→paid and later partial payments do not register.  
**Work:**
1. On UPDATE (and/or dedicated payment signal), detect `financial_status` / transaction deltas  
2. Call payment registration path (reuse `apply_workflow` payment slice or extract `sync_payments_from_payload`)  
3. Keep idempotency on `shopify_transaction_id`  
4. Tests: pending→paid; second partial payment; no double pay on replay  

**Estimate:** M (1–2 days)  
**Files:** `models/order_queue.py`, `services/order_import_service.py`, `services/order_update_service.py`, tests `test_p567_financial.py` / new

---

### WP-B — Stock availability gate before confirm (P0)

**Scenarios:** BC-02  
**Problem:** `action_confirm()` has no stock check.  
**Work:**
1. Store flag e.g. `confirm_require_stock` (default off for PetSpot retail; **on** for iZone)  
2. Before confirm: for each stockable line, check free qty (reuse stock type logic from `shopify_stock_service`)  
3. If insufficient: leave SO draft/sent, queue fail or `shopify_stock_block` flag + clear log — **do not** silent confirm  
4. Tests: in-stock confirm; zero-stock block; multi-line partial stock  

**Estimate:** M (1 day)  
**Files:** `services/order_import_service.py` (`apply_workflow`), `models/shopify_store.py`, views, tests

---

### WP-C — Post-shipment cancel / failed delivery return (P1)

**Scenarios:** SO-06, BC-04, BC-08  
**Problem:** Cancel after done picking does not create return; seller-shipping-cost is ops-only.  
**Work:**
1. When cancel/refund and outgoing picking is **done**: create return picking per `refund_restock_mode` (align with refund path)  
2. Document ops rule: shipping cost on seller = manual journal (N/A) unless finance wants a fee product  
3. Optional: “failed delivery” reason mapping from cancel reason  
4. Tests: cancel after validate → return + CN; refund after deliver → restock  

**Estimate:** M (1–2 days)  
**Files:** `services/refund_sync_service.py`, `services/return_picking_service.py`, tests

---

### WP-D — Shipping address sync on update (P1)

**Scenarios:** SO-14  
**Work:** On order update, sync shipping address to SO partner_shipping / picking destination when edit mode allows; create/update child partner as on import.  
**Estimate:** S (0.5–1 day)  
**Files:** `services/order_update_service.py`, `services/order_service.py` (`_get_or_create_partner`)

---

### WP-E — Inbound tracking (Shopify → Odoo) (P1)

**Scenarios:** SO-20  
**Work:** In `ShopifyFulfillmentService.handle_fulfillment`, write tracking numbers to `stock.picking.carrier_tracking_ref` (and packages if present).  
**Estimate:** S (0.5 day)  
**Files:** `services/fulfillment_service.py`, tests `test_p4_fulfillment.py`

---

### WP-F — Damaged replacement without restock (P1)

**Scenarios:** BC-05  
**Work:**
1. Extend exchange wizard / store policy: `replacement_restock_mode` = `restock` | `no_restock` (damaged)  
2. Or document + small helper: create replacement SO **without** CN restock when flag set  
3. Tests for no-restock path  

**Estimate:** S–M (0.5–1 day)  
**Files:** `models/shopify_exchange_wizard.py`, store config, tests `test_exchange_wizard.py`

---

### WP-G — Order notes + currency (P2)

**Scenarios:** SO-25, SO-26  
**Work:** Map Shopify `note` → SO `note`/`client_order_ref` or chatter; map order currency when multi-currency enabled.  
**Estimate:** S–M  
**Files:** `services/order_service.py`, `models/sale_order.py`

---

### WP-H — Order tags (P2)

**Scenarios:** SO-27  
**Work:** Store Shopify order tags on SO (Many2many tag model or Char); update on sync.  
**Estimate:** S  
**Files:** `models/sale_order.py`, order services

---

### WP-I — Reopen / archive policy (P2)

**Scenarios:** SO-23, SO-24  
**Work:** Define policy with Selim (reopen = new SO vs uncancel); implement archive as status flag only.  
**Estimate:** M (needs client decision first → **Blocked** until answered)  
**Files:** webhook handler, order update, sale.order fields

---

### WP-J — Shipping method & payment method change (P2)

**Scenarios:** SO-15, SO-16  
**Work:** On update, adjust shipping product line / carrier; optionally remap payment journal if unpaid.  
**Estimate:** M  
**Files:** `order_update_service.py`, gateway mapping

---

## 3. Explicitly out of scope (this plan)

| Item | Reason |
|------|--------|
| Live BOSTA API | User: simulate delivery in Odoo |
| InstaPay auto +1 day calendar | Simulate payment date manually (BC-10 N/A) |
| Cash EOD cron | Manual post (BC-11) |
| External PR USD (BC-01) | Not Shopify connector |
| Full WP70 UAT checklist beyond these 38 | Separate; reuse after P0/P1 |

---

## 4. Suggested implementation order

```
Sprint 1 (P0)     WP-A payment-on-update → WP-B stock gate
Sprint 2 (P1)     WP-C post-ship return → WP-D address → WP-E tracking → WP-F damaged
Sprint 3 (P2)     WP-G notes/currency → WP-H tags → WP-J method changes
Decision gate     WP-I reopen/archive (after Selim policy)
Then              PetSpot UAT (#410) → iZone parity deploy
```

---

## 5. Acceptance for “ready for iZone UAT”

- [ ] WP-A + WP-B merged & tested on PetSpot Test  
- [ ] WP-C + WP-E at least for cancel/refund after ship + tracking  
- [ ] Scenario tracker §7 updated with Supported for all P0 IDs  
- [ ] Manual simulation runbook confirmed for Paymob/InstaPay/Cash/BOSTA  

---

## 6. Effort rollup

| Priority | WPs | Rough effort |
|----------|-----|--------------|
| P0 | A, B | ~2–3 days |
| P1 | C, D, E, F | ~3–4.5 days |
| P2 | G, H, J (+ I if unblocked) | ~2–4 days |
| **Total** | | **~7–11.5 days** engineering |

---

## 7. OpenProject children to create (when implementation starts)

| Proposed subject | Priority | Parent |
|------------------|----------|--------|
| WP-A: Register payments on Shopify order update | P0 | #412 or #409 |
| WP-B: Stock gate before SO confirm (iZone flag) | P0 | |
| WP-C: Return picking on cancel/refund after delivery | P1 | |
| WP-D: Sync shipping address on order update | P1 | |
| WP-E: Write Shopify tracking to Odoo picking | P1 | |
| WP-F: Damaged replacement without restock | P1 | |
| WP-G/H/J: Notes, currency, tags, method changes | P2 | |

*(Implementation WPs can be created when you say “start coding”.)*
