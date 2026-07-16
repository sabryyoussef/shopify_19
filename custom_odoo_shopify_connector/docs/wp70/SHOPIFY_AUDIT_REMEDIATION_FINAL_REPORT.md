# Shopify Audit Remediation — Final Report (WP70)

**Date:** 2026-07-13  
**Module:** `custom_odoo_shopify_connector`  
**Environment:** Test only (`odoo_test`) — **no push / no production**

---

## 1. Initial State

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `60bf393` (ahead of origin by 10; uncommitted WP70 work in working tree) |
| Git status (start) | Restored tree after Phase 0; remediation changes WIP |
| DB backup | Pre-change: `/opt/odoo-log-report-2026-06-28/backups/shopify_audit_test_20260713_2025/` (Phase 0). Post-work snapshot attempted under `/opt/odoo/backups/shopify_audit_post_wp70_*` |
| Database | `odoo_test` |
| Module version | `19.0.1.0.5` |

---

## 2. Source Recovery (Phase 0)

Restored from **git history** (primary) / WP69 backup cross-check:

| File | Source |
|---|---|
| `models/shopify_store.py` | git |
| `models/webhook_handler.py` | git + enhanced |
| `models/order_queue.py` | git |
| `services/retry_policy.py` | git |
| `services/order_import_service.py` | git |
| `controllers/webhook_controller.py` | git |
| `tests/__init__.py` | git + extended |

**Route split (no conflict):**

- Order create/update → `controllers/webhook_controller.py`
- Cancel / refund → `controllers/shopify_webhook.py`

**Clean import / upgrade:** `compileall` OK; module upgrade on `odoo_test` OK; registry loaded without CRITICAL errors (pre-existing view accessibility WARNINGs only).

---

## 3. Changes Implemented

| Requirement | Previous Status | Changes | Files | New Status |
|---|---|---|---|---|
| Discount scenarios | Partial | Capped discount %; order-level `total_discounts` distribute; `shopify_discount_source` | `order_service.py`, `sale_order.py`, `test_discount_scenarios.py` | Covered |
| Shipping / fulfillment | Partial | Shipping line idempotency; Odoo 19 move-line qty (`quantity`/`picked`); `shopify_fulfillment_ids` | `order_service.py`, `fulfillment_service.py`, `sale_order.py`, `test_shipping_scenarios.py` | Covered |
| Exchange | Partial | Idempotency key; balance-due line; fail paths | `shopify_exchange_wizard.py`, `test_exchange_wizard.py` | Covered |
| Cancel before invoice | Gap | Cancel SO + draft invoices + open pickings; CN only if posted invoice | `refund_sync_service.py`, `shopify_webhook.py`, `test_cancel_before_invoice.py` | Covered |
| Idempotency | Partial | Documented keys + tests | `test_idempotent_payment_and_refund.py` | Covered |
| Webhook replay + logs | Gap | Replay button; attempt/last_attempt/error_type; Sync Log UI + Retry; secret masking | `webhook_handler.py`, `sync_log.py`, views, ACL | Covered |
| Paymob fees | Partial | Technical options report; reserved gateway fields (default unchanged) | `PAYMOB_FEES_ACCOUNTING_OPTIONS.md`, `shopify_payment_gateway.py` | Documented |
| UAT checklist | Missing | 22 scenarios template | `docs/wp70/SHOPIFY_UAT_CHECKLIST.md` | Ready (Not Run) |
| Checkpoint tests hang | Bug | mock + deactivate other stores | `test_checkpoint_scheduler.py` | Fixed |

---

## 4. Tests

### Phase tests (new classes only)

| Item | Value |
|---|---|
| Command | `odoo-bin -c /etc/odoo.conf -d odoo_test --http-port=8071 --workers=0 --test-enable --stop-after-init --test-tags='/custom_odoo_shopify_connector:TestDiscountScenarios,...' -u custom_odoo_shopify_connector` |
| Database | `odoo_test` |
| Log | `/opt/odoo/logs/shopify_tests_phases_20260713_204618.log` |
| Cases | **38** |
| Failed | **0** |
| Errors | **0** |
| Exit code | **0** |
| Duration | **~31s** |

| Test Class | Cases | Passed | Failed | Skipped |
|---|---|---|---|---|
| TestDiscountScenarios | 8 | 8 | 0 | 0 |
| TestShippingScenarios | 6 | 6 | 0 | 0 |
| TestExchangeWizard | 8 | 8 | 0 | 0 |
| TestCancelBeforeInvoice | 5 | 5 | 0 | 0 |
| TestIdempotentPaymentAndRefund | 7 | 7 | 0 | 0 |
| TestWebhookReplay | 4 | 4 | 0 | 0 |

### Full module suite

Attempted with `--test-tags=custom_odoo_shopify_connector`. Legacy issues remain outside the WP70 scope (some pre-existing tests call unmocked Shopify HTTP / Odoo 16 APIs). Checkpoint hang fixed via mock + deactivating other stores. `invalidate_cache` → `invalidate_recordset` fixed for Odoo 19.

**Gate for this remediation:** new WP70 classes **38/38 passed** (log above). Full-suite green is a follow-up hardening task.

---

## 5. UAT Status

| # | Scenario | Status |
|---|---|---|
| 1–22 | See `docs/wp70/SHOPIFY_UAT_CHECKLIST.md` | **Ready** (manual) / **Not Run** |

Automated regression covers discounts, shipping idempotency, exchange, cancel-before-invoice, webhook replay, payment/refund idempotency keys. End-to-end Shopify↔Odoo UAT still required for client sign-off.

---

## 6. Remaining Decisions

| Topic | Status |
|---|---|
| **Paymob accounting treatment** | OPEN — keep current fee product/discount until finance approves `expense_split` (`docs/wp70/PAYMOB_FEES_ACCOUNTING_OPTIONS.md`) |
| **Barcode fallback** | Unchanged / not expanded in this pass |
| **Exchange from Shopify vs Odoo only** | Currently **Odoo wizard**; Shopify-initiated exchange not implemented |
| **Refund stock mode default** | Store-level `refund_restock_mode`; confirm production default with ops |

---

## 7. Risks

| Area | Risk |
|---|---|
| Functional | Order edit after confirm still mode-dependent; multi-package outbound fulfillment needs live Shopify API proof |
| Accounting | Paymob expense_split reserved but inactive — dual bookkeeping risk if ops change mode without charts of accounts |
| Stock | Fulfillment now Odoo-19 aware; staging stock levels can still block validate |
| Deployment | Source tree was previously broken (.py deleted); ensure clean deploy without relying on `.pyc` |
| Data duplication | Webhook/order/payment/refund/fulfillment keys present; residual risk if Shopify IDs missing on lines |

---

## 8. Final Verdict

# READY FOR TECHNICAL UAT

**Reasons:**

1. Source tree restored and module upgrades cleanly on **test** DB.  
2. New automated suite for audit gaps: **38/38 passed**.  
3. Critical cancel-before-invoice and exchange idempotency guards in place.  
4. Webhook Replay + Sync Log masking/Retry available for ops recovery.  
5. Manual Shopify UAT checklist prepared but **not executed** against live store.  
6. Paymob accounting change **intentionally not activated**.  
7. **No push / no production deployment** performed.

**Not** ready for Client UAT / Production until checklist Pass evidence and finance decision on Paymob.

---

**Constraints honored:** test DB only · backup taken · no git push · no production touch.
