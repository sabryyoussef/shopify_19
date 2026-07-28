# Code analysis — 38 testing scenarios vs `custom_odoo_shopify_connector`

**Date:** 2026-07-28  
**Module:** `/home/sabry/odoo_base/base_odoo_19/projects/pet_spot_elsahel/custom_odoo_shopify_connector`  
**Scenarios:** `/home/sabry/iZone_Shopify_Odoo_Testing_Scenarios.md`  
**OpenProject:** [#411](https://master.tailcf9988.ts.net:10081/work_packages/411)  
**Dev plan:** `/home/sabry/iZone_Shopify_Odoo_Development_Plan.md` · [#412](https://master.tailcf9988.ts.net:10081/work_packages/412)

### Verdict legend
| Verdict | Meaning |
|---------|---------|
| **Supported** | Present in code; UAT with config/simulation |
| **Partial** | Exists but incomplete for scenario acceptance |
| **Gap** | Missing — needs development |
| **N/A** | Out of connector scope (manual Odoo / ops); no code change required for stated simulation rule |

---

## Summary scorecard

| Verdict | Count | IDs |
|---------|------:|-----|
| Supported | 14 | SO-01, SO-05*, SO-07, SO-08, SO-17, SO-18, SO-19, SO-21, SO-22, BC-03*, BC-06*, BC-09*, BC-11*, SO-04* |
| Partial | 14 | SO-02, SO-03, SO-05†, SO-06, SO-09–16, SO-20, BC-04, BC-05, BC-07, BC-08 |
| Gap | 7 | BC-02, SO-23, SO-24, SO-25, SO-26, SO-27, (+ payment-on-update as SO-03 core gap) |
| N/A | 3 | BC-01, BC-10‡, (BC-09/11 partly N/A under simulation) |

\* Supported for primary path; see notes.  
† SO-05 Supported before invoice; listed Partial only for edge cases after done picking if cancel used instead of refund.  
‡ InstaPay +1 day settlement date = N/A under simulation (manual date) unless auto calendar requested.

**Development needed:** yes — see development plan (P0–P2).

---

## Block BC — Business cycles

| ID | Title | Verdict | Code refs / notes |
|----|-------|---------|-------------------|
| BC-01 | External PR + USD | **N/A** | Not Shopify connector. Manual Odoo purchase/PR. No connector change. |
| BC-02 | Confirm SO only if stock available | **Gap** | `apply_workflow` → `action_confirm()` with no qty gate (`order_import_service.py`). Stock settings are for **inventory export** only. |
| BC-03 | Ship / validate picking (simulate BOSTA) | **Supported** | Manual validate picking + `ShopifyFulfillmentService` for Shopify→Odoo. Simulation = Odoo picking validate. |
| BC-04 | Not received → return; cost on seller → Cancel | **Partial** | Cancel/refund paths exist (`RefundSyncService.cancel_order_from_shopify`). Done pickings not auto-returned on cancel; need refund restock or manual return. No “seller bears shipping” accounting automation. |
| BC-05 | Damaged → replacement; no restock damaged | **Partial** | `shopify.exchange.wizard` creates CN + replacement SO; restock follows `refund_restock_mode`. No first-class “replace without restock/CN of damaged” policy flag. Workaround: `credit_note_only` + manual replacement SO. |
| BC-06 | Full prepayment then deliver | **Supported** | Paid at import → `decide_financial_actions` / `apply_workflow` registers payment; then validate delivery. |
| BC-07 | Partial prepay; remainder on delivery | **Partial** | Partial at **import** supported (`partial_payment_mode`, `extract_paid_amount`). Later “collect rest on delivery” is not automatic — needs payment update path (SO-03 gap) or **manual** second payment in Odoo (fits simulation rule). |
| BC-08 | Prepaid but not received → return + refund | **Partial** | Compose cancel + refund + restock. Works if refund webhook/manual CN; done-picking return depends on `refund_restock_mode`. No dedicated “failed delivery” operation. |
| BC-09 | Paymob = immediate | **Supported** / **N/A*** | Gateway map `online` + Paymob heuristics + journal (`shopify.payment.gateway`, `_gateway_category`). Simulation = register Paymob payment in Odoo. Live API not required. |
| BC-10 | InstaPay = +1 working day | **N/A** (config Supported) | Gateway can map to `bank`/InstaPay journal. **No** settlement-date offset automation. Simulation = set payment date +1 day manually. |
| BC-11 | Cash = EOD / next day | **Supported** / **N/A*** | COD gateway + `invoice_timing=on_delivery`. Simulation = post cash payment EOD manually. No auto EOD cron. |

---

## Block SO — Sales integration (Selim 1–27)

| ID | Title | Verdict | Code refs / notes |
|----|-------|---------|-------------------|
| SO-01 | Happy path paid / COD | **Supported** | `OrderImportService.import_shopify_order` + workflow; COD invoice-on-delivery supported. |
| SO-02 | Pending payment | **Partial** | Unpaid create works if workflow does not force pay. Depends on `financial_status` + workflow config; needs UAT to lock COD/pending presets. |
| SO-03 | Pending → Paid update | **Gap** | `order_queue` UPDATE path calls `OrderUpdateService.update_order_from_payload` only — **does not** re-run `apply_workflow` / register payment (`order_queue.py` ~348–351). |
| SO-04 | Partial payment installments | **Partial** | Supported at import; subsequent installments need SO-03 fix or manual payments. |
| SO-05 | Cancel before shipment | **Supported** | `cancel_order_from_shopify`; cancels draft invoices + open pickings + SO; tests `test_cancel_before_invoice.py`. |
| SO-06 | Cancel after shipment | **Partial** | CN if posted invoice (`cancel_sync_mode=credit_note`); **done** pickings not auto-returned — use refund/restock path. |
| SO-07 | Full refund | **Supported** | `RefundSyncService.sync_refund_from_webhook` + restock modes; idempotent `shopify_refund_id`. |
| SO-08 | Partial refund | **Supported** | Line-level CN via `refund_sync_mode=line_level`. |
| SO-09 | Qty update before ship | **Partial** | `OrderUpdateService` + `order_edit_sync_mode`; blocked when paid / after invoice unless `with_adjustments`. |
| SO-10 | Remove item | **Partial** | Same as SO-09 (in-place line sync, state gates). |
| SO-11 | Add item | **Partial** | Same as SO-09. |
| SO-12 | Price update | **Partial** | Same; paid orders → `STRATEGY_REFUND_REQUIRED` (log only). |
| SO-13 | Discount update | **Partial** | Same. |
| SO-14 | Shipping address update | **Gap** | Update path does not rewrite delivery/shipping address on SO/picking. |
| SO-15 | Shipping method update | **Partial** | Shipping lines on **create** only; no carrier/method remap on update. |
| SO-16 | Payment method update | **Partial** | Stores `shopify_payment_gateway` string; does not switch journal / re-register. |
| SO-17 | Full fulfillment | **Supported** | `ShopifyFulfillmentService.handle_fulfillment`. |
| SO-18 | Partial fulfillment / backorder | **Supported** | Backorder confirmation path in fulfillment service. |
| SO-19 | Split shipment | **Supported** | Multi-fulfillment ids tracked. |
| SO-20 | Tracking number (Shopify→Odoo) | **Gap** | Odoo→Shopify tracking supported (`ShippingService`); inbound fulfillment does **not** set `carrier_tracking_ref`. |
| SO-21 | Customer update | **Supported** | `ShopifyCustomerSync` + partner on order import. |
| SO-22 | Duplicate webhook | **Supported** | `shopify.webhook.event` unique + queue/SO/payment/refund idempotency. |
| SO-23 | Reopened cancelled order | **Gap** | No reopen/uncancel/replacement policy. |
| SO-24 | Archived order | **Gap** | No `closed_at` / archive status-only sync. |
| SO-25 | Foreign currency on SO | **Gap** | SO uses company/pricelist currency; Shopify order currency not mapped. |
| SO-26 | Order notes | **Gap** | `note` / `note_attributes` not stored on SO. |
| SO-27 | Order/customer tags | **Gap** | Product tags only; no SO/order tag sync. |

---

## Architecture notes (relevant to gaps)

```
Webhook → shopify.webhook.handler → shopify.order.queue
  CREATE  → OrderImportService.import → apply_workflow (confirm/invoice/pay)
  UPDATE  → OrderUpdateService.update_order_from_payload  ❌ no apply_workflow
  CANCEL  → RefundSyncService.cancel_order_from_shopify
  REFUND  → RefundSyncService.sync_refund_from_webhook
  FULFILL → ShopifyFulfillmentService.handle_fulfillment
```

Store flags already useful for UAT: `cancel_sync_mode`, `order_edit_sync_mode`, `partial_payment_mode`, `refund_restock_mode`, `sale_auto_workflow_id`, gateway journals.

---

## What does **not** need code (simulation / ops)

Per agreed rule (mirror in Odoo):

- Paymob / InstaPay / Cash **timing** → register payments with correct journal + date  
- BOSTA delivery → validate picking  
- BC-01 purchase PR → manual Odoo  

These stay out of the development plan unless you later ask for automation.

---

## Recommended next step

1. Approve development plan priorities (P0–P2)  
2. Implement P0 on PetSpot Test branch  
3. Re-run scenarios UAT (#410)  
