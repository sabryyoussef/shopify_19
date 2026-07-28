# iZone Shopify ↔ Odoo — Master brief

**One file that explains everything** (WhatsApp docs → testing scenarios → code analysis → development plan → OpenProject).

| | |
|--|--|
| **Date** | 2026-07-28 |
| **Client group** | WhatsApp *Izone - Internal BIS* |
| **Codebase** | `custom_odoo_shopify_connector` (PetSpot first → iZone A-Zone) |
| **Test env first** | PetSpot Test · `pet_spot_elsahel_test` · port **8028** · https://test.drpaws.ai · Shopify **`ucbah1-5e`** |
| **Then** | iZone A-Zone / Shopify `izone-eg` |
| **OpenProject parent** | [#409](https://master.tailcf9988.ts.net:10081/work_packages/409) under `#170 izone_azone_parent` · project **#12** |

---

## 1. What happened

Selim and Abdelrahman sent **written examination / test scenarios** in the iZone internal WhatsApp group. We:

1. Downloaded the attachments  
2. Built a **canonical test scenario pack** (38 scenarios)  
3. Analyzed each against the **Shopify–Odoo connector code**  
4. Wrote a **development plan** for gaps only  
5. Tracked everything in OpenProject  

**Agreed test rule:** do **not** call live BOSTA / InstaPay / Paymob APIs. For those rails, **create or update the same outcome inside Odoo** (payment journals + validate delivery pickings). Shopify order sync on PetSpot Test still uses the real connector.

---

## 2. Source files (WhatsApp)

| # | File | From | What it contains |
|---|------|------|------------------|
| S1 | `iZone Testing.docx` | Abdelrahman | Original business cycles (purchase, sales/BOSTA, Paymob/InstaPay/Cash) |
| S2 | `iZone Case Scenarios.docx` | Abdelrahman | **Updated** S1 + **prepay / partial / refund if not received** |
| S3 | `Shopify_Odoo_Sales_Integration_Test_Scenarios.docx` | Selim | **27** Shopify↔Odoo sales scenarios + event→object matrix |

Saved on master:

- `/home/sabry/iZone_Testing.docx` (+ `.txt`)
- `/home/sabry/iZone_Case_Scenarios.docx` (+ `.txt`)
- `/home/sabry/Shopify_Odoo_Sales_Integration_Test_Scenarios.docx` (+ `.txt`)

S2 supersedes S1 for business cycles. S3 is the detailed integration checklist.

---

## 3. Simulation rule (payments & delivery)

| Client concept | How we test in Odoo |
|----------------|---------------------|
| **Paymob** (immediate) | Register payment **today** with Paymob journal/method |
| **InstaPay** (~1 working day) | Register payment with date **+1 business day** |
| **Cash** (BOSTA collects EOD/next day) | Register Cash/COD payment at **EOD or next day** |
| **BOSTA delivery** | Validate **outgoing picking** only — no BOSTA API |
| **Not received / return to iZone** | Return/cancel picking + cancel SO in Odoo |
| **Damaged product** | Replacement SO/delivery; **do not** restock damaged qty |

---

## 4. Testing scenarios (38 total)

Canonical pack: `/home/sabry/iZone_Shopify_Odoo_Testing_Scenarios.md`  
Copy under connector: `…/custom_odoo_shopify_connector/docs/izone-uat/TESTING_SCENARIOS.md`

### Block BC — Business cycles (from S1/S2) — 11 scenarios

| ID | Scenario |
|----|----------|
| BC-01 | External PR registered on system; pay in **USD** |
| BC-02 | SO from Shopify confirmed **only if stock available** |
| BC-03 | After stock check → “hand to BOSTA” = validate picking; delivery cost on customer |
| BC-04 | Not received → return to warehouse; shipping cost on seller → Cancelled |
| BC-05 | Damaged → send replacement; damaged **not** returned |
| BC-06 | **Full prepayment** then deliver (nothing else due) |
| BC-07 | **Partial prepayment**; remainder collected on delivery |
| BC-08 | Prepaid but **not received** → return + **refund** paid amount |
| BC-09 | Paymob settlement = immediate |
| BC-10 | InstaPay settlement = +1 working day |
| BC-11 | Cash settlement = EOD / next day |

### Block SO — Sales integration (from S3 / Selim) — 27 scenarios

| ID | Selim # | Scenario |
|----|---------|----------|
| SO-01 | 1 | Basic paid order (happy path); COD → invoice without payment |
| SO-02 | 2 | Pending payment → SO + delivery, no payment |
| SO-03 | 3 | Pending → Paid → register payment on existing SO |
| SO-04 | 4 | Partial payments / installments |
| SO-05 | 5 | Cancel before shipment |
| SO-06 | 6 | Cancel after shipment → return + credit note |
| SO-07 | 7 | Full refund |
| SO-08 | 8 | Partial refund |
| SO-09 | 9 | Quantity update before ship |
| SO-10 | 10 | Remove item |
| SO-11 | 11 | Add item |
| SO-12 | 12 | Price update |
| SO-13 | 13 | Discount update |
| SO-14 | 14 | Shipping address update |
| SO-15 | 15 | Shipping method update |
| SO-16 | 16 | Payment method update |
| SO-17 | 17 | Full fulfillment |
| SO-18 | 18 | Partial fulfillment / backorder |
| SO-19 | 19 | Split shipment |
| SO-20 | 20 | Tracking number |
| SO-21 | 21 | Customer update |
| SO-22 | 22 | Duplicate webhook (no duplicate SO) |
| SO-23 | 23 | Reopened cancelled order |
| SO-24 | 24 | Archived order |
| SO-25 | 25 | Foreign currency |
| SO-26 | 26 | Order notes |
| SO-27 | 27 | Tags |

### Shopify → Odoo matrix (Selim)

| Shopify event | Odoo object | Action |
|---------------|-------------|--------|
| Order Created | Sales Order | Create |
| Order Updated | Sales Order | Update |
| Order Cancelled | Sales Order | Cancel |
| Payment Paid / Partial | Payment | Register / Add |
| Refund / Partial Refund | Credit Note | Create / Partial |
| Fulfillment / Partial | Delivery | Validate / Backorder |
| Address / Shipping / Tracking | Partner / Delivery | Update |
| Item add/remove/qty/price/discount | SO line / Invoice | Create/Delete/Update/Recalc |
| Customer Updated | Partner | Update |

---

## 5. Code analysis result

**Module analyzed:**  
`/home/sabry/odoo_base/base_odoo_19/projects/pet_spot_elsahel/custom_odoo_shopify_connector`

**Full report:** `/home/sabry/iZone_Shopify_Odoo_Code_Analysis.md`

### Pipeline today

```
Webhook → shopify.webhook.handler → shopify.order.queue
  CREATE  → OrderImportService → apply_workflow (confirm / invoice / pay)
  UPDATE  → OrderUpdateService only     ← does NOT re-run apply_workflow
  CANCEL  → RefundSyncService.cancel_order_from_shopify
  REFUND  → RefundSyncService.sync_refund_from_webhook
  FULFILL → ShopifyFulfillmentService.handle_fulfillment
```

### Scorecard

| Verdict | ~Count | Meaning |
|---------|-------:|---------|
| **Supported** | 14 | Ready for UAT with config / simulation |
| **Partial** | 14 | Exists but incomplete for acceptance |
| **Gap** | 7 | Missing — needs development |
| **N/A** | 3 | Out of connector / simulate manually |

### Every scenario vs code

| ID | Title | Verdict | Notes |
|----|-------|---------|-------|
| BC-01 | External PR USD | **N/A** | Not Shopify connector — manual Odoo |
| BC-02 | Stock gate before confirm | **Gap** | `action_confirm()` with no qty check |
| BC-03 | Ship / validate picking | **Supported** | Manual picking + fulfillment service |
| BC-04 | Not received → cancel/return | **Partial** | Cancel/refund exist; done picking not auto-returned |
| BC-05 | Damaged replacement no restock | **Partial** | Exchange wizard exists; no clean “no restock” policy |
| BC-06 | Full prepay then deliver | **Supported** | Paid at import + workflow |
| BC-07 | Partial + rest on delivery | **Partial** | Partial at import OK; later payment needs update fix |
| BC-08 | Failed delivery → refund | **Partial** | Compose cancel+refund; no dedicated op |
| BC-09 | Paymob immediate | **Supported / N/A** | Gateway map exists; simulate payment in Odoo |
| BC-10 | InstaPay +1 day | **N/A** | No settlement calendar; set payment date manually |
| BC-11 | Cash EOD | **Supported / N/A** | COD workflow; post cash manually |
| SO-01 | Happy path | **Supported** | Import + workflow + COD invoice-on-delivery |
| SO-02 | Pending payment | **Partial** | Config-dependent |
| SO-03 | Pending → paid | **Gap** | UPDATE skips payment registration |
| SO-04 | Partial installments | **Partial** | Import yes; follow-on needs SO-03 fix |
| SO-05 | Cancel before ship | **Supported** | Cancels SO / drafts / open pickings |
| SO-06 | Cancel after ship | **Partial** | CN yes; auto-return weak |
| SO-07 | Full refund | **Supported** | RefundSyncService + restock modes |
| SO-08 | Partial refund | **Supported** | Line-level CN |
| SO-09–13 | Qty/add/remove/price/discount | **Partial** | Works with `order_edit_sync_mode`; blocked if paid |
| SO-14 | Address update | **Gap** | Not rewritten on update |
| SO-15 | Shipping method update | **Partial** | Shipping lines mainly on create |
| SO-16 | Payment method update | **Partial** | Stores gateway string; no journal switch |
| SO-17 | Full fulfillment | **Supported** | |
| SO-18 | Partial / backorder | **Supported** | |
| SO-19 | Split shipment | **Supported** | |
| SO-20 | Tracking Shopify→Odoo | **Gap** | Odoo→Shopify tracking exists; inbound missing |
| SO-21 | Customer update | **Supported** | |
| SO-22 | Duplicate webhook | **Supported** | Strong idempotency |
| SO-23 | Reopened order | **Supported** | Manual review only (19.0.1.9.0) — no auto-reset |
| SO-24 | Archived order | **Supported** | Metadata only via `closed_at` (19.0.1.9.0) |
| SO-25 | Foreign currency | **Gap** | Order currency not mapped to SO |
| SO-26 | Order notes | **Gap** | Not stored on SO |
| SO-27 | Tags | **Gap** | Product tags only |

**Conclusion:** connector is strong on create / cancel-before-ship / refund / fulfillment / idempotency. **Development is required** for payment-on-update, stock gate, and several P1/P2 items.

---

## 6. Development plan

**Full plan:** `/home/sabry/iZone_Shopify_Odoo_Development_Plan.md`  
**Estimate:** ~7–11.5 engineering days (P0 → P1 → P2)

### P0 — must build first (~2–3 days)

| WP | What | Fixes scenarios | OpenProject |
|----|------|-----------------|-------------|
| **WP-A** | On Shopify order **update**, register new/delta payments (reuse workflow payment logic; keep txn idempotency) | SO-03, SO-04, BC-07 | [#413](https://master.tailcf9988.ts.net:10081/work_packages/413) |
| **WP-B** | Store flag `confirm_require_stock` — block confirm if free qty insufficient (on for iZone) | BC-02 | [#414](https://master.tailcf9988.ts.net:10081/work_packages/414) |

### P1 — should build next (~3–4.5 days)

| WP | What | Fixes |
|----|------|-------|
| **WP-C** | Return picking when cancel/refund after **done** delivery | SO-06, BC-04, BC-08 |
| **WP-D** | Sync shipping address on order update | SO-14 |
| **WP-E** | Write Shopify tracking → `carrier_tracking_ref` on picking | SO-20 |
| **WP-F** | Damaged replacement **without** restock policy | BC-05 |

### P2 — later (~2–4 days)

| WP | What | Fixes |
|----|------|-------|
| **WP-G** | Order notes + currency | SO-25, SO-26 |
| **WP-H** | Order tags | SO-27 |
| **WP-I** | Reopen / archive — **implemented** (Archive=metadata; Reopen=manual review) | SO-23, SO-24 |
| **WP-J** | Shipping method & payment method change | SO-15, SO-16 |

### Explicitly skip (per simulation rule)

- Live **BOSTA** API  
- **InstaPay** auto +1-day calendar job  
- **Cash** EOD cron  
- **BC-01** external PR (manual Odoo / not connector)

### Suggested build order

```
Sprint 1  →  WP-A + WP-B (P0) on PetSpot Test
Sprint 2  →  WP-C, D, E, F (P1)
Sprint 3  →  WP-G, H, J (+ I after client decision)
Then      →  PetSpot UAT → iZone A-Zone parity
```

---

## 7. How we will UAT (after P0 / needed P1)

1. Use PetSpot Test + Shopify `ucbah1-5e` (never mix with `izone-eg` during Phase 1)  
2. Pull/create orders via connector where possible  
3. For Paymob / InstaPay / Cash / “BOSTA”: **only Odoo updates**  
4. Log each ID: Shopify #, Odoo SO, payment/delivery action, Pass/Fail  
5. Gate before iZone: B1-1/SO-01, BC-02 (after WP-B), SO-03 (after WP-A), cancel/refund smoke, one prepay case  

Execution WP: [#410](https://master.tailcf9988.ts.net:10081/work_packages/410)

---

## 8. OpenProject map

| WP | Role | URL |
|----|------|-----|
| **#170** | Parent epic `izone_azone_parent` | [link](https://master.tailcf9988.ts.net:10081/work_packages/170) |
| **#409** | Umbrella: scenarios → analysis → plan → UAT | [link](https://master.tailcf9988.ts.net:10081/work_packages/409) |
| **#410** | UAT execution on PetSpot (after fixes) | [link](https://master.tailcf9988.ts.net:10081/work_packages/410) |
| **#411** | Code analysis (DONE) | [link](https://master.tailcf9988.ts.net:10081/work_packages/411) |
| **#412** | Development plan | [link](https://master.tailcf9988.ts.net:10081/work_packages/412) |
| **#413** | P0 WP-A payment on update | [link](https://master.tailcf9988.ts.net:10081/work_packages/413) |
| **#414** | P0 WP-B stock gate | [link](https://master.tailcf9988.ts.net:10081/work_packages/414) |

---

## 9. Related files on master (detail packs)

| File | Purpose |
|------|---------|
| **This file** `/home/sabry/iZone_Shopify_Odoo_MASTER.md` | Single explanation of everything |
| `/home/sabry/iZone_Shopify_Odoo_Testing_Scenarios.md` | Full scenario templates + analysis tracker |
| `/home/sabry/iZone_Shopify_Odoo_Code_Analysis.md` | Deep code verdicts |
| `/home/sabry/iZone_Shopify_Odoo_Development_Plan.md` | WP-A…J build plan |
| `/home/sabry/iZone_Testing_Plan_PetSpot_First.md` | Earlier PetSpot-first runbook |
| `…/custom_odoo_shopify_connector/docs/izone-uat/` | Copies of scenarios / analysis / plan next to code |

---

## 10. Bottom line

1. Client sent **3 docs** → **38 test scenarios** → code analysis → development plan.  
2. **All build phases done on PetSpot Test** (`custom_odoo_shopify_connector` **19.0.1.9.0**):  
   - P0: payment-on-update + stock gate  
   - P1: return-after-cancel, address sync, inbound tracking, damaged no-restock  
   - P2: notes/currency, tags, shipping/payment method update  
   - **WP-I:** Archive = metadata only · Reopen = manual review only  
3. **UAT PASS** on https://test.drpaws.ai (original 7/7 + WP-I SO-23/24) + Shopify order **#1003** on `ucbah1-5e`.  
4. Automated tests: **37/37** (`shopify_wp_ab` + `shopify_wp_p1p2` + `shopify_wp_i`).  
5. Proof pack: `/home/sabry/iZone_UAT_Proof_20260728/` (+ `wp_i/`).  
6. Payments/courier still **simulated in Odoo** (no live BOSTA/InstaPay).  
7. **Stop:** do not deploy to iZone until **19.0.1.9.0** is explicitly approved; then enable `confirm_require_stock` on iZone store.
