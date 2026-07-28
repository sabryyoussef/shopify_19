# Shopify ↔ Odoo — Testing Scenarios (canonical)

**Status:** Ready for code analysis → development plan  
**Date:** 2026-07-28  
**OpenProject:** [#409](https://master.tailcf9988.ts.net:10081/work_packages/409) · execution [#410](https://master.tailcf9988.ts.net:10081/work_packages/410)  
**First test env:** PetSpot Test (`pet_spot_elsahel_test` / https://test.drpaws.ai / Shopify `ucbah1-5e`)  
**Target code:** `custom_odoo_shopify_connector` (PetSpot copy → then iZone A-Zone)

---

## 1. Purpose

This file is the **single scenario pack** derived from client WhatsApp documents.  
Next steps (separate deliverables):

1. **Code analysis** — for each scenario mark `Supported` / `Partial` / `Gap` against connector code  
2. **Development plan** — only for `Partial` + `Gap` items (WPs, priority, estimate)

Do **not** implement until the code-analysis pass is done.

---

## 2. Source documents

| # | File | Author | Role |
|---|------|--------|------|
| S1 | `iZone Testing.docx` | Abdelrahman | Original business cycles |
| S2 | `iZone Case Scenarios.docx` | Abdelrahman | **Updated** cycles + prepay/partial/refund |
| S3 | `Shopify_Odoo_Sales_Integration_Test_Scenarios.docx` | Selim | 27 sales-integration scenarios + matrix |

Local copies: `/home/sabry/iZone_Testing.docx`, `/home/sabry/iZone_Case_Scenarios.docx`, `/home/sabry/Shopify_Odoo_Sales_Integration_Test_Scenarios.docx`

---

## 3. Test rules

### 3.1 Simulation (mandatory)

| External concept | How we test |
|------------------|-------------|
| Paymob | Register payment in Odoo **immediately** (Paymob journal/method) — no live gateway required |
| InstaPay | Register payment in Odoo with date **+1 working day** |
| Cash / COD | Register cash payment **EOD or next day** after “delivery” |
| BOSTA delivery | **Validate delivery picking** in Odoo only — no BOSTA API |
| BOSTA return | Return/cancel picking + cancel SO in Odoo |
| Damaged goods | Replacement SO/delivery; **do not** restock damaged qty |

Shopify ↔ Odoo **order sync** on PetSpot Test uses the real connector where available.

### 3.2 Result legend (fill during code analysis)

| Code | Meaning |
|------|---------|
| `Supported` | Behaviour exists in code; UAT should pass with config only |
| `Partial` | Exists but incomplete / wrong edge cases |
| `Gap` | Missing — needs development |
| `N/A` | Out of connector scope (manual Odoo / ops policy) |
| `Blocked` | Needs client decision before build |

### 3.3 Scenario template

Each scenario below uses:

- **ID** — stable id for OP / code map  
- **Sources** — S1 / S2 / S3 + Selim #  
- **Trigger** — Shopify event and/or manual Odoo step  
- **Expected Odoo** — acceptance criteria  
- **Analysis** — empty until code review (`Verdict` / `Code refs` / `Notes`)

---

## 4. Block BC — Business cycles (S1 + S2)

### BC-01 — External purchase / PR in USD

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | Manual in Odoo: create external PR (or PO) and register USD payment |
| Expected Odoo | PR/PO exists; payment in **USD**; posted correctly |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-02 — SO from Shopify only if stock available

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | Shopify order for in-stock vs zero-stock mapped product |
| Expected Odoo | In stock → SO confirmable; out of stock → **not** confirmed / blocked per rules; no wrong stock move |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-03 — After stock check → ship (simulate BOSTA)

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | Confirm SO → validate outgoing picking; delivery cost on customer |
| Expected Odoo | Picking done; stock out; shipping charge on customer |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-04 — Not received → return to warehouse; shipping cost on seller → Cancelled

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | After ship: return/cancel delivery + cancel SO in Odoo (simulate BOSTA return) |
| Expected Odoo | SO cancelled/returned; shipping cost borne by seller (iZone); stock handled per return rules |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-05 — Damaged product → replacement; damaged not returned

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | After delivered: create replacement SO/delivery; do **not** receive damaged qty |
| Expected Odoo | Replacement delivered; stock **not** increased by damaged return |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-06 — Full prepayment then deliver

| Field | Value |
|-------|-------|
| Sources | S2 (new) |
| Trigger | Register **full** payment in Odoo → validate delivery |
| Expected Odoo | Invoice fully paid before/at ship; nothing due after delivery |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-07 — Partial prepayment; remainder on delivery

| Field | Value |
|-------|-------|
| Sources | S2 (new) |
| Trigger | Register partial payment → validate delivery → register **remaining** as Cash/COD |
| Expected Odoo | Two (or more) payments; invoice fully paid after delivery collection |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-08 — Prepaid (partial or full) but not received → return + refund

| Field | Value |
|-------|-------|
| Sources | S2 (new) |
| Trigger | After partial/full prepay: simulate failed delivery → return to warehouse → refund paid amount |
| Expected Odoo | Return/cancel; shipping cost on seller; **refund** of paid amount (partial or full); stock restored as configured |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-09 — Paymob settlement = immediate

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | After receive (or per policy): register payment with **Paymob** method, date = today |
| Expected Odoo | Payment posted immediately; reconciles to invoice/bank journal |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-10 — InstaPay settlement = +1 working day

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | Register payment with **InstaPay** method, date = next business day |
| Expected Odoo | Payment exists with delayed settlement date; correct journal |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### BC-11 — Cash settlement = EOD / next day

| Field | Value |
|-------|-------|
| Sources | S1, S2 |
| Trigger | After delivery: register **Cash** payment at EOD or next day |
| Expected Odoo | Not marked bank-paid until cash entry posted |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

---

## 5. Block SO — Sales integration (S3 — Selim 1–27)

### SO-01 — Basic order (happy path) — Selim #1

| Field | Value |
|-------|-------|
| Trigger | Shopify: customer creates **paid** order (or COD) |
| Expected Odoo | Create customer if needed; SO; Delivery; Invoice; **if paid** → register payment; **if COD** → invoice **without** payment |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-02 — Pending payment — Selim #2

| Field | Value |
|-------|-------|
| Trigger | Shopify order with pending payment |
| Expected Odoo | SO + Delivery; **do not** register payment |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-03 — Pending → paid — Selim #3

| Field | Value |
|-------|-------|
| Trigger | Pending order becomes Paid on Shopify (or register payment in Odoo) |
| Expected Odoo | Update existing SO; register payment; no second SO |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-04 — Partial payment / installments — Selim #4

| Field | Value |
|-------|-------|
| Trigger | Multiple payment transactions / manual partial payments |
| Expected Odoo | Reflect partial payments until invoice fully paid |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-05 — Cancel before shipment — Selim #5

| Field | Value |
|-------|-------|
| Trigger | Cancel on Shopify before fulfillment |
| Expected Odoo | Cancel SO, Delivery, Invoice/draft; release reserved stock; no CN if no posted invoice |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-06 — Cancel after shipment — Selim #6

| Field | Value |
|-------|-------|
| Trigger | Delivered order cancelled |
| Expected Odoo | Return picking + Credit Note |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-07 — Full refund — Selim #7

| Field | Value |
|-------|-------|
| Trigger | Entire order refunded on Shopify |
| Expected Odoo | Credit Note + refund payment + return picking + restock |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-08 — Partial refund — Selim #8

| Field | Value |
|-------|-------|
| Trigger | Part of order refunded |
| Expected Odoo | Partial CN + partial stock return |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-09 — Quantity update before shipping — Selim #9

| Field | Value |
|-------|-------|
| Trigger | Qty changed on Shopify before ship |
| Expected Odoo | Update SO lines, Delivery, Invoice if allowed |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-10 — Remove item — Selim #10

| Field | Value |
|-------|-------|
| Trigger | Item removed from Shopify order |
| Expected Odoo | Remove SO line; update related documents |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-11 — Add item — Selim #11

| Field | Value |
|-------|-------|
| Trigger | New item added on Shopify |
| Expected Odoo | Add SO line; update delivery/invoice |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-12 — Price update — Selim #12

| Field | Value |
|-------|-------|
| Trigger | Price changed on Shopify |
| Expected Odoo | Recalculate totals and taxes |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-13 — Discount update — Selim #13

| Field | Value |
|-------|-------|
| Trigger | Coupon/discount changed |
| Expected Odoo | Recalculate invoice and taxes |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-14 — Shipping address update — Selim #14

| Field | Value |
|-------|-------|
| Trigger | Customer changes address |
| Expected Odoo | Update delivery address on SO/picking |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-15 — Shipping method update — Selim #15

| Field | Value |
|-------|-------|
| Trigger | Shipping method changes |
| Expected Odoo | Update delivery method and shipping charges |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-16 — Payment method update — Selim #16

| Field | Value |
|-------|-------|
| Trigger | e.g. COD → card/Paymob |
| Expected Odoo | Update payment journal/method if applicable |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-17 — Full fulfillment — Selim #17

| Field | Value |
|-------|-------|
| Trigger | Order fulfilled (Shopify and/or validate picking in Odoo) |
| Expected Odoo | Delivery validated |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-18 — Partial fulfillment — Selim #18

| Field | Value |
|-------|-------|
| Trigger | Only part shipped |
| Expected Odoo | Backorder for remaining items |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-19 — Split shipment — Selim #19

| Field | Value |
|-------|-------|
| Trigger | Items shipped from multiple warehouses / multiple fulfillments |
| Expected Odoo | Multiple pickings |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-20 — Tracking number — Selim #20

| Field | Value |
|-------|-------|
| Trigger | Tracking added (Shopify or manual on picking) |
| Expected Odoo | Delivery tracking field updated |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-21 — Customer update — Selim #21

| Field | Value |
|-------|-------|
| Trigger | Customer info changes on Shopify |
| Expected Odoo | Partner data updated |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-22 — Duplicate webhook — Selim #22

| Field | Value |
|-------|-------|
| Trigger | Same event received twice |
| Expected Odoo | No duplicate SO; ignore or idempotent update |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-23 — Reopened order — Selim #23

| Field | Value |
|-------|-------|
| Trigger | Cancelled order reopened |
| Expected Odoo | Reopen or controlled replacement per integration policy |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-24 — Archived order — Selim #24

| Field | Value |
|-------|-------|
| Trigger | Order archived on Shopify |
| Expected Odoo | Status sync only; no accounting action |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-25 — Foreign currency — Selim #25

| Field | Value |
|-------|-------|
| Trigger | Foreign currency order (e.g. USD) |
| Expected Odoo | Currency consistent across SO, Invoice, Payment |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-26 — Order notes — Selim #26

| Field | Value |
|-------|-------|
| Trigger | Customer note added |
| Expected Odoo | Note stored on SO |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

### SO-27 — Tags — Selim #27

| Field | Value |
|-------|-------|
| Trigger | Order/customer tags updated |
| Expected Odoo | Tags updated if supported |
| Analysis | Verdict: _TBD_ · Code refs: · Notes: |

---

## 6. Integration matrix (S3)

| Shopify event | Odoo object | Action | Maps to |
|---------------|-------------|--------|---------|
| Order Created | Sales Order | Create | SO-01, SO-02 |
| Order Updated | Sales Order | Update | SO-03, SO-09–16 |
| Order Cancelled | Sales Order | Cancel | SO-05, SO-06 |
| Payment Paid | Payment | Register | SO-01, SO-03, BC-09 |
| Partial Payment | Payment | Add | SO-04, BC-07 |
| Refund | Credit Note | Create | SO-07, BC-08 |
| Partial Refund | Credit Note | Partial | SO-08 |
| Fulfillment | Delivery | Validate | SO-17, BC-03 |
| Partial Fulfillment | Delivery | Backorder | SO-18 |
| Address Changed | Partner/SO | Update | SO-14 |
| Shipping Changed | Delivery | Update | SO-15 |
| Tracking Added | Delivery | Update | SO-20 |
| Item Added | SO Line | Create | SO-11 |
| Item Removed | SO Line | Delete | SO-10 |
| Quantity Changed | SO Line | Update | SO-09 |
| Price Changed | SO Line | Update | SO-12 |
| Discount Changed | SO Line/Invoice | Recalculate | SO-13 |
| Customer Updated | Partner | Update | SO-21 |

---

## 7. Code-analysis tracker (filled 2026-07-28)

Full report: `/home/sabry/iZone_Shopify_Odoo_Code_Analysis.md`  
Dev plan: `/home/sabry/iZone_Shopify_Odoo_Development_Plan.md`

| ID | Title | Verdict | Priority if Gap/Partial | Code refs | Dev plan item |
|----|-------|---------|-------------------------|-----------|---------------|
| BC-01 | External PR USD | N/A | — | Manual Odoo | Skip |
| BC-02 | Stock gate | Gap | P0 | `apply_workflow` no qty check | WP-B |
| BC-03 | Ship / picking | Supported | — | Fulfillment + manual validate | — |
| BC-04 | Not received cancel | Partial | P1 | Cancel; done picking no auto-return | WP-C |
| BC-05 | Damaged replacement | Partial | P1 | Exchange wizard + restock mode | WP-F |
| BC-06 | Full prepay | Supported | — | Import paid workflow | — |
| BC-07 | Partial + rest COD | Partial | P0 | Import partial OK; later pay weak | WP-A |
| BC-08 | Failed delivery refund | Partial | P1 | Refund+restock compose | WP-C |
| BC-09 | Paymob immediate | Supported/N/A | — | Gateway map; simulate pay | Skip API |
| BC-10 | InstaPay +1d | N/A | — | Manual payment date | Skip |
| BC-11 | Cash EOD | Supported/N/A | — | COD + manual cash | Skip |
| SO-01 | Happy path | Supported | — | OrderImportService | — |
| SO-02 | Pending payment | Partial | P2 | Config-dependent | UAT config |
| SO-03 | Pending → paid | Gap | P0 | UPDATE skips apply_workflow | WP-A |
| SO-04 | Partial payments | Partial | P0 | Import yes; follow-on needs WP-A | WP-A |
| SO-05 | Cancel before ship | Supported | — | cancel_order_from_shopify | — |
| SO-06 | Cancel after ship | Partial | P1 | CN yes; return weak | WP-C |
| SO-07 | Full refund | Supported | — | RefundSyncService | — |
| SO-08 | Partial refund | Supported | — | line_level CN | — |
| SO-09 | Qty update | Partial | P2 | order_edit_sync_mode gates | Config/UAT |
| SO-10 | Remove item | Partial | P2 | same | Config/UAT |
| SO-11 | Add item | Partial | P2 | same | Config/UAT |
| SO-12 | Price update | Partial | P2 | blocked if paid | Config/UAT |
| SO-13 | Discount update | Partial | P2 | same | Config/UAT |
| SO-14 | Address update | Gap | P1 | not on update path | WP-D |
| SO-15 | Shipping method | Partial | P2 | create-only shipping lines | WP-J |
| SO-16 | Payment method | Partial | P2 | string only | WP-J |
| SO-17 | Full fulfillment | Supported | — | FulfillmentService | — |
| SO-18 | Partial fulfillment | Supported | — | backorder | — |
| SO-19 | Split shipment | Supported | — | multi fulfillment ids | — |
| SO-20 | Tracking inbound | Gap | P1 | Odoo→Shopify only today | WP-E |
| SO-21 | Customer update | Supported | — | CustomerSync | — |
| SO-22 | Duplicate webhook | Supported | — | webhook event unique | — |
| SO-23 | Reopened order | Supported | P2 | pass | WP-I (manual review) |
| SO-24 | Archived order | Supported | P2 | pass | WP-I (metadata) |
| SO-25 | Currency | Gap | P2 | not mapped | WP-G |
| SO-26 | Order notes | Gap | P2 | not mapped | WP-G |
| SO-27 | Tags | Gap | P2 | product tags only | WP-H |

**Totals:** Supported ~14 · Partial ~14 · Gap ~7 · N/A ~3 — **development plan required (P0–P2).**


## 8. Next deliverables

1. ~~Code analysis report~~ → `/home/sabry/iZone_Shopify_Odoo_Code_Analysis.md` (#411)  
2. ~~Development plan~~ → `/home/sabry/iZone_Shopify_Odoo_Development_Plan.md` (#412)  
3. **Implement P0** (WP-A payment-on-update, WP-B stock gate) on PetSpot Test  
4. **UAT execution** on PetSpot Test (#410) using simulation rules (§3.1)

---

## 9. Suggested analysis order

1. SO-01, SO-02, SO-05, SO-07, SO-08, SO-22 (core connector)  
2. SO-09–13 (order edit)  
3. SO-17–20 (fulfillment)  
4. BC-02–05, BC-06–08 (iZone business cycles)  
5. BC-09–11 (payment timing — mostly config/N/A if journals exist)  
6. Remaining SO-* misc  
