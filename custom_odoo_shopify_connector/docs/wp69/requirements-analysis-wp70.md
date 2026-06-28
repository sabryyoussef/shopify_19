# Requirements Analysis — iZone Shopify ↔ Odoo Integration

**Document version:** 2.0 (live OpenProject + Excel)  
**Date:** 2026-06-28  
**OpenProject sources:**

| WP | Subject | Role |
|----|---------|------|
| [#69](https://master.tailcf9988.ts.net:10081/work_packages/69) | iZone requirements: Shopify-Odoo integration spec (21 tasks + Amazon) | **Requirements spec** + Excel attachment |
| [#70](https://master.tailcf9988.ts.net:10081/work_packages/70) | Odoo 19 / Shopify iZone EG — Final Remediation Report | **Infrastructure/remediation delivery** |

**Project:** AZone - WorldPosta  
**Excel source:** `izone-requirement-2026-06-28.xlsx` (attachment on WP #69)  
**Local copy:** `/opt/odoo-log-report-2026-06-28/docs/attachments/izone-requirement-2026-06-28.xlsx`  
**Environment:** Odoo 19 Enterprise, DB `odoo_test`, store **iZone EG**

---

## 1. Executive summary

The Excel specification defines **21 integration tasks** focused on **order accounting**: final pricing, shipping lines, Paymob fees, returns/credit notes, sync automation, and logging. The current connector (`custom_odoo_shopify_connector`) **fully or partially covers ~11 tasks**; **~8 tasks need new development** (fees engine, exchange logic, unified view, Amazon); **2 tasks** need verification/UAT against live Shopify payloads.

Remediation work documented in **WP #70** addressed **platform stability** (WebSocket, crons, product import, logging, ACLs) — separate from but **prerequisite** to reliable order/finance integration.

| Status | Count |
|--------|-------|
| Met | 6 |
| Partial | 9 |
| Not met | 5 |
| Out of scope (Amazon) | 1 |

---

## 2. Requirements from Excel (`izone-requirement-2026-06-28.xlsx`)

| ID | Task | Trigger | Expected output | Notes (from spec) |
|----|------|---------|-----------------|-------------------|
| 1 | Sync Final Price Only | Order Sync | SO line at actual price | Send line price **before discount** + discount value on same line |
| 2 | Shipping as Line Item | Order Sync | "Shipping" line with amount | Fixed SKU or delivery product |
| 3 | Payment Method Mapping | Order Sync | Odoo field for COD / Paymob | Prerequisite for fee logic |
| 4 | Conditional Fees Logic | Order Sync | Calculated fee (Paymob 5% + fixed) | Must be configurable |
| 5 | Add Fees Line (Optional Mode) | Invoice Creation | "Payment Fees" line or negative discount | Optional per config |
| 6 | Return Detection | Webhook / Sync | Trigger credit note creation | Critical |
| 7 | Auto Credit Note Creation | Return Sync | Credit note linked to invoice | Do not edit original invoice |
| 8 | Partial Return Handling | Return Sync | Partial credit note | Match refunded lines |
| 9 | Exchange Scenario Logic | Return Sync | Credit note + new invoice | Link refund + new item |
| 10 | Invoice & Credit Linking | Post Processing | Relations in Odoo | Traceability |
| 11 | Unified Order View | UI / Backend | Single screen for invoices + returns | Optional UI |
| 12 | Auto Sync (Near Realtime) | Shopify events | Immediate Odoo update | Webhooks + polling fallback |
| 13 | Historical Sync Logic | Initial Setup | Import from start date only | Avoid full history overload |
| 14 | Transaction Logging | All operations | Log model with success/fail | Debugging |
| 15 | Error Handling System | Failure | Retry queue + clear errors | Stability |
| 16 | Payment Net vs Gross Handling | Order Sync | Store order total + net received | Skip if data unavailable |
| 17 | Refund Amount Logic | Return Sync | Correct refund after shipping rules | Per Shopify |
| 18 | Multi-Channel Journal Tagging | Order Sync | Journal/tag per source | Scalable |
| 19 | Delivered vs Invoiced Sync | Fulfillment Sync | Status fields updated | Tracking |
| 20 | Configurable Rules Engine | Admin Config | Dynamic fees %, fixed fees, modes | Reduce hardcoding |
| 21 | Amazon integration | — | Same as Shopify | Future channel |

---

## 3. Traceability matrix (Excel → connector → status)

| ID | Requirement | Priority | Status | Evidence in codebase / ops | Gap / next step |
|----|-------------|----------|--------|---------------------------|-----------------|
| 1 | Sync Final Price Only | Must | **Partial** | `order_service._compute_discount_pct()` applies discount % on `price_unit` from Shopify line | Spec wants explicit pre-discount price + discount amount fields; verify against live Paymob orders |
| 2 | Shipping as Line Item | Must | **Met** | `order_service._create_shipping_lines()` using `store.delivery_product_id` | Ensure delivery product configured on iZone store |
| 3 | Payment Method Mapping | Must | **Met** | `order_import_service` reads `payment_gateway_names`; `shopify.payment.gateway` model; COD/Paymob/INSTAPAY configured | UAT per gateway name matching |
| 4 | Conditional Fees Logic (Paymob 5%) | Must | **Not met** | No Paymob fee calculation found in connector | Implement configurable % + fixed fee when gateway = Paymob |
| 5 | Add Fees Line (Optional Mode) | Should | **Not met** | No payment-fees line on invoice | Add optional SO/invoice line or discount per config |
| 6 | Return Detection | Must | **Partial** | Order import checks `financial_status` refunded/partially_refunded; webhooks for orders | Dedicated refund/return webhook handling needs review |
| 7 | Auto Credit Note Creation | Must | **Partial** | `shopify_service` calls `_reverse_moves()` on refunded status | Verify does not alter original invoice; test partial refunds |
| 8 | Partial Return Handling | Must | **Partial** | Reverse moves may not match line-level refunds | Map Shopify `refund_line_items` to credit note lines |
| 9 | Exchange Scenario Logic | Should | **Not met** | No exchange (refund + new order) orchestration | Design linked credit note + new SO workflow |
| 10 | Invoice & Credit Linking | Should | **Partial** | Standard Odoo reversal links exist | Expose clear relation on SO / shopify order map |
| 11 | Unified Order View | Could | **Not met** | No custom unified view model | Optional Studio or custom action |
| 12 | Auto Sync Near Realtime | Must | **Met** | `manage_orders_webhook` + order queue cron (~1 min) | WP #70 fixed WebSocket for UI; sync path operational |
| 13 | Historical Sync Logic | Should | **Partial** | `last_order_import_time` on store; import filters exist | Confirm start-date filter on initial sync UI/cron |
| 14 | Transaction Logging | Must | **Met** | `shopify.sync.log` via `shopify.sync.log.mixin`; API logs | Phase 5 reduced product log noise; order logs retained |
| 15 | Error Handling System | Must | **Met** | Order/product/customer queues + `action_retry_failed`; cron skip without token (Phase 2–3) | Retry 7,481 historical product queue lines |
| 16 | Payment Net vs Gross | Could | **Not met** | No dedicated net-received fields on SO | Add optional fields if Shopify/payment data available |
| 17 | Refund Amount Logic | Should | **Partial** | Refund via financial status + reverse moves | Validate shipping deduction rules vs Shopify |
| 18 | Multi-Channel Journal Tagging | Should | **Partial** | `shopify.sale.auto.workflow` → `sales_journal_id`; gateway → `odoo_journal_id` | Tag all Shopify orders consistently |
| 19 | Delivered vs Invoiced Sync | Should | **Partial** | Fulfillment service; `update_shipping_sync_enabled`; picking `shopify_shipping_status` | Map fulfillment → invoice/delivery state explicitly |
| 20 | Configurable Rules Engine | Must | **Partial** | Workflows + payment gateways + financial status mappings | Missing fees rules engine (ties to #4, #5, #20) |
| 21 | Amazon integration | Future | **Not met** | Shopify-only connector | Separate epic; same patterns as Shopify |

---

## 4. Remediation delivery (WP #70) — platform prerequisites

These items from WP #70 enable reliable execution of the Excel requirements:

| Phase | Deliverable | Relevance to Excel reqs |
|-------|-------------|------------------------|
| 1 WebSocket | nginx + gevent :8072 | Stable UI for order/accounting review (#11) |
| 2–3 Crons | Order + customer import without token crash | Reliable sync (#12, #15) |
| 4 Products | Variant integrity fix | Product lines on orders (#1, #8) |
| 5 Logging | DEBUG for product noise | Keeps #14 usable in production |
| 6 Hygiene | Wizard ACLs, field labels | Refund wizard (#7) operable |

**Git commits:** `80f99ed` → `666cf7c` on `main`.

---

## 5. Gap analysis & recommended implementation order

### Priority 1 — Order accounting (Excel #1–5, #20)

1. **Price line model** — Store Shopify `price` + `discount_allocations` explicitly on SO lines (#1).
2. **Paymob fees engine** — Config on store: `% fee`, fixed fee, apply as line vs discount (#4, #5, #20).
3. **UAT** — Import real Paymob + COD orders; reconcile SO total vs Shopify.

### Priority 2 — Returns (Excel #6–10, #17)

4. **Refund webhook** — Listen to `refunds/create`; queue processing (#6).
5. **Line-level credit notes** — Partial refunds (#8); shipping in refund calc (#17).
6. **Exchange workflow** — Design before build (#9).

### Priority 3 — Operations (Excel #11–19)

7. **Historical import cutoff** — Admin setting for start date (#13).
8. **Fulfillment ↔ invoice status** — Explicit field sync (#19).
9. **Unified order dashboard** — If stakeholders require (#11).

### Priority 4 — Future

10. **Amazon channel** (#21) — New integration epic.

---

## 6. OpenProject work package cross-reference

| WP | Title | Status | Relationship |
|----|-------|--------|--------------|
| 67 | Shopify ↔ Odoo integration | New | Parent epic |
| 69 | iZone requirements spec (21 tasks) | New | **Requirements source** |
| 70 | Final Remediation Report | Closed | **Platform fixes delivered** |

**Suggested OP actions:**

- Link WP #69 ↔ #70 as *relates to*
- Create child tasks under #69 for each **Not met** requirement (#4, #5, #9, #11, #16, #21)
- Move #4 + #5 + #20 into one development WP: *Paymob fees & rules engine*

---

## 7. Sign-off checklist

- [ ] Excel row-by-row review with iZone finance (Arabic notes in column D understood)
- [ ] Paymob order import UAT (price + fees)
- [ ] COD order import UAT (no fees)
- [ ] Partial refund test on Shopify → Odoo credit note
- [ ] Product queue retry completed (7,481 historical lines)
- [ ] Production promotion plan for WP #70 nginx/connector changes

---

*Generated from OpenProject API (WP #69, #70) + Excel attachment. Regenerate: `python3 docs/fetch-wp70-and-analyze.py` (extend for WP #69).*
