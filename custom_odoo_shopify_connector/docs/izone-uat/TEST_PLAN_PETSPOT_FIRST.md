# iZone business-cycle testing plan (PetSpot first)

**OpenProject:** [#409](https://master.tailcf9988.ts.net:10081/work_packages/409) · child [#410](https://master.tailcf9988.ts.net:10081/work_packages/410)  
**WhatsApp group:** Izone - Internal BIS

## Source documents (2026-07-28)

| # | File | From | Local copy |
|---|------|------|------------|
| 1 | `iZone Testing.docx` | Abdelrahman | `/home/sabry/iZone_Testing.docx` |
| 2 | `iZone Case Scenarios.docx` (**updated**) | Abdelrahman | `/home/sabry/iZone_Case_Scenarios.docx` |
| 3 | `Shopify_Odoo_Sales_Integration_Test_Scenarios.docx` | Selim | `/home/sabry/Shopify_Odoo_Sales_Integration_Test_Scenarios.docx` |

Extracted text: same paths with `.txt`.

Doc **2** supersedes doc **1** for business cycles (adds prepayment / partial / failed-delivery refund). Doc **3** is the full Shopify↔Odoo sales integration matrix (27 scenarios).

---

## Integration rule (unchanged)

**No live BOSTA / InstaPay / Paymob API.** Mirror outcomes **inside Odoo**:

| Concept | In Odoo |
|---------|---------|
| Paymob | Register payment **now** (Paymob journal/method) |
| InstaPay | Register payment with settlement **+1 working day** |
| Cash / COD | Register cash at **EOD or next day** |
| BOSTA delivery | Validate **delivery picking** manually |
| Not received | Cancel/return picking + cancel SO in Odoo |
| Damaged | Replacement SO/delivery; **no** restock of damaged unit |
| Partial / prepay | Register partial payments on invoice until fully paid |

Shopify order sync on PetSpot Test stays real.

| Env | Role |
|-----|------|
| **Phase 1 — PetSpot Test** | `pet_spot_elsahel_test` · 8028 · https://test.drpaws.ai · Shopify `ucbah1-5e` |
| **Phase 2 — iZone** | Same Odoo-simulation pattern on A-Zone / client |

---

## Phase 0 — Prep

- [ ] PetSpot Test Odoo up
- [ ] Shopify = **`ucbah1-5e`** only
- [ ] Journals/methods labeled: Paymob, InstaPay, Cash/COD
- [ ] Delivery picking works without courier API
- [ ] Mapped in-stock + zero-stock products
- [ ] Run log ready

---

## Block A — Business cycles (from Case Scenarios — updated)

### A1 Purchase
| ID | Scenario | Odoo action | Pass? |
|----|----------|-------------|-------|
| A1-1 | External PR registered on system; pay in **USD** | Create/register PR (or PO flow) + USD payment in Odoo | |

> On PetSpot: simulate with a USD purchase/payment record if PR module not used; full PR on iZone Phase 2.

### A2 Sales + delivery (simulate BOSTA)
| ID | Scenario | Odoo action | Pass? |
|----|----------|-------------|-------|
| A2-1 | SO pulled from Shopify; confirm only if stock available | SO from Shopify; block if no stock | |
| A2-2 | After stock check → “hand to BOSTA” | Validate outgoing picking; delivery cost on customer | |
| A2-3 | Not received → return to iZone; shipping cost on iZone → Cancelled | Cancel/return in Odoo; cost stays iZone | |
| A2-4 | Damaged → send replacement; damaged **not** returned | Replacement SO/delivery; no restock damaged | |

### A3 Prepayments / partial (NEW from updated Case Scenarios)
| ID | Scenario | Odoo action | Pass? |
|----|----------|-------------|-------|
| A3-1 | **Full prepayment** then deliver | Register full payment before/at ship; deliver; nothing else due | |
| A3-2 | **Partial payment** then deliver; rest on delivery | Register partial payment; on “deliver” register remaining (Cash/COD journal) | |
| A3-3 | Partial (or full) paid but **not received** → return + **refund** paid amount | Return/cancel in Odoo; shipping cost on iZone; **refund** partial or full paid amount | |

### A4 Payments timing «الفلوس بتسمع امتا»
| ID | Scenario | Odoo action | Pass? |
|----|----------|-------------|-------|
| A4-1 | Paymob = immediate | Payment today, Paymob method | |
| A4-2 | InstaPay = +1 working day | Payment date next business day | |
| A4-3 | Cash = EOD / next day | Cash payment posted EOD or next day | |

---

## Block B — Shopify ↔ Odoo sales integration (from Selim’s 27 scenarios)

Simulate payment/delivery in Odoo where noted. Prefer PetSpot connector for Shopify events.

### B1 Create / pay
| ID | Selim # | Scenario | Expected in Odoo | Pass? |
|----|---------|----------|------------------|-------|
| B1-1 | 1 | Basic paid order (happy path) | Customer, SO, Delivery, Invoice, Payment (if paid); if COD → invoice **without** payment yet | |
| B1-2 | 2 | Pending payment | SO + Delivery; **no** payment | |
| B1-3 | 3 | Pending → Paid | Update SO; register payment | |
| B1-4 | 4 | Partial payment (installments) | Multiple payments until invoice fully paid | |

### B2 Cancel / refund / return
| ID | Selim # | Scenario | Expected in Odoo | Pass? |
|----|---------|----------|------------------|-------|
| B2-1 | 5 | Cancel before shipment | Cancel SO, Delivery, Invoice/draft; release stock | |
| B2-2 | 6 | Cancel after shipment | Return picking + Credit Note | |
| B2-3 | 7 | Full refund | CN + refund payment + return picking + restock | |
| B2-4 | 8 | Partial refund | Partial CN + partial stock return | |

### B3 Order line / price edits
| ID | Selim # | Scenario | Expected in Odoo | Pass? |
|----|---------|----------|------------------|-------|
| B3-1 | 9 | Quantity update before ship | Update SO lines, Delivery, Invoice if allowed | |
| B3-2 | 10 | Remove item | Remove SO line; update related docs | |
| B3-3 | 11 | Add item | Add SO line; update delivery/invoice | |
| B3-4 | 12 | Price update | Recalculate totals/taxes | |
| B3-5 | 13 | Discount update | Recalculate invoice/taxes | |

### B4 Address / shipping / payment method
| ID | Selim # | Scenario | Expected in Odoo | Pass? |
|----|---------|----------|------------------|-------|
| B4-1 | 14 | Shipping address update | Update delivery address | |
| B4-2 | 15 | Shipping method update | Update delivery method + charges | |
| B4-3 | 16 | Payment method update (e.g. COD → card) | Update payment journal if applicable | |

### B5 Fulfillment (simulate courier in Odoo)
| ID | Selim # | Scenario | Expected in Odoo | Pass? |
|----|---------|----------|------------------|-------|
| B5-1 | 17 | Full fulfillment | Validate delivery | |
| B5-2 | 18 | Partial fulfillment | Backorder for remainder | |
| B5-3 | 19 | Split shipment | Multiple pickings | |
| B5-4 | 20 | Tracking number | Store tracking on delivery (manual field OK) | |

### B6 Customer / resilience / misc
| ID | Selim # | Scenario | Expected in Odoo | Pass? |
|----|---------|----------|------------------|-------|
| B6-1 | 21 | Customer update | Update partner | |
| B6-2 | 22 | Duplicate webhook | No duplicate SO | |
| B6-3 | 23 | Reopened cancelled order | Reopen or controlled replacement per policy | |
| B6-4 | 24 | Archived order | Status sync only; no accounting action | |
| B6-5 | 25 | Foreign currency | Currency consistent on SO/Invoice/Payment | |
| B6-6 | 26 | Order notes | Store on SO | |
| B6-7 | 27 | Tags | Update tags if supported | |

### Integration matrix (Selim) — quick ref

| Shopify event | Odoo object | Action |
|---------------|-------------|--------|
| Order Created | Sales Order | Create |
| Order Updated | Sales Order | Update |
| Order Cancelled | Sales Order | Cancel |
| Payment Paid | Payment | Register |
| Partial Payment | Payment | Add |
| Refund / Partial Refund | Credit Note | Create / Partial |
| Fulfillment / Partial | Delivery | Validate / Backorder |
| Address / Shipping / Tracking | Partner/SO/Delivery | Update |
| Item add/remove/qty/price/discount | SO line / Invoice | Create/Delete/Update/Recalc |
| Customer Updated | Partner | Update |

---

## Suggested run order (PetSpot)

**Day 1 — smoke (gate)**  
1. B1-1 happy path  
2. A2-1 / A2-2 stock + validate delivery in Odoo  
3. A4-1 Paymob payment in Odoo  
4. A4-2 InstaPay (+1 day) in Odoo  
5. B2-1 cancel before ship  

**Day 2 — money & returns**  
6. A3-1 full prepay → deliver  
7. A3-2 partial → rest on delivery (Cash in Odoo)  
8. A3-3 paid but not received → return + refund  
9. B2-3 / B2-4 full & partial refund  
10. A2-4 damaged replacement  

**Day 3 — edits & fulfillment**  
11. B3-* qty/add/remove/price/discount  
12. B5-* full/partial/split fulfillment + tracking  
13. B1-2/B1-3/B1-4 pending & partial pay  
14. B6-2 duplicate webhook + remaining misc  

### Phase 1 gate (before calling iZone “ready”)

- [ ] B1-1, A2-2, A4-1, A4-2  
- [ ] B2-1 and (B2-3 or B2-4)  
- [ ] A3-2 or A3-3  
- [ ] A2-4 or B2-2  

---

## Phase 2 — iZone

Repeat Block A + critical Block B on iZone DB/store with the **same Odoo-simulation rule**. Add A1-1 USD PR on client system.

---

## Run log

| ID | Date | Shopify # | Odoo SO | Odoo payment / delivery action | Result | Notes |
|----|------|-----------|---------|--------------------------------|--------|-------|
| B1-1 | | | | | | |
| A2-2 | | | | validate picking | | |
| A4-1 | | | | Paymob now | | |
| A4-2 | | | | InstaPay +1d | | |
| A3-2 | | | | partial + COD rest | | |
| A3-3 | | | | return + refund | | |
| B2-1 | | | | | | |
| B2-4 | | | | | | |
