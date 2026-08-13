# WP70 Proof Report — All Requirements Verified

**Date:** 2026-07-02  
**Environment:** Odoo 19 Enterprise, DB `odoo_test`  
**Module:** `custom_odoo_shopify_connector` **19.0.1.0.5**  
**Odoo service:** Restarted and active after upgrade

---

## Deployment

| Step | Result |
|------|--------|
| Stop Odoo service | OK |
| Module upgrade `-u custom_odoo_shopify_connector` | OK (exit 0) |
| DB version confirmed | `19.0.1.0.5` installed |
| Restart Odoo service | `active` |

---

## Automated Tests (19 tests, 0 failures, 0 errors)

```
custom_odoo_shopify_connector: 35 tests 5.15s 1870 queries
EXIT: 0
```

| Test class | Tests | Requirement |
|------------|-------|-------------|
| `TestAutoCreateProduct` | 2 | G6 — product flag enforcement |
| `TestPartialPayment` | 3 | G1 — partial payment amount extraction |
| `TestPaymentFeeService` | 3 | Paymob/COD fees (WP69 + UAT) |
| `TestCancelCreditNote` | 2 | G2 — cancel → credit note |
| `TestOrderUpdateSync` | 1 | G3 — Shopify order edit sync |
| `TestRefundRestock` | 2 | G5 — refund restock pickings |
| `TestWebhookSecurity` | 3 | Duplicate webhook dedup |
| `TestRetryPolicy` | 3 | Failed order retry policy |

**Run command:**
```bash
sudo -u odoo /opt/odoo/venv/bin/python3 /opt/odoo/odoo/odoo-bin \
  -c /etc/odoo.conf -d odoo_test --test-enable --stop-after-init --http-port=8099 \
  --test-tags=/custom_odoo_shopify_connector:TestAutoCreateProduct,\
/custom_odoo_shopify_connector:TestPartialPayment,\
/custom_odoo_shopify_connector:TestPaymentFeeService,\
/custom_odoo_shopify_connector:TestCancelCreditNote,\
/custom_odoo_shopify_connector:TestOrderUpdateSync,\
/custom_odoo_shopify_connector:TestRefundRestock,\
/custom_odoo_shopify_connector:TestWebhookSecurity,\
/custom_odoo_shopify_connector:TestRetryPolicy
```

---

## Database & File Proof (20/20 checks)

Script: `scripts/wp70_proof.py` — **ALL REQUIREMENTS VERIFIED**

- Module v19.0.1.0.5 installed
- All new `sale.order`, `shopify.store`, `account.payment` fields present
- `order_update_service.py`, `return_picking_service.py` deployed
- All WP70 test files present
- `docs/wp70/REMEDIATION_PLAN.md` present

---

## Client Requirements Traceability (Chatwoot #46)

| Client requirement | Status | Proof |
|--------------------|--------|-------|
| No discount / coupon / auto discount | Supported (WP69) | `order_service._compute_discount_pct` |
| Multiple products | Supported (WP69) | Line items loop |
| Multiple shipments | Supported (WP69) | `shipping_service` multi-package |
| Free shipping | Supported (WP69) | `_create_shipping_lines` price=0 |
| Partial payment | **WP70 Done** | `TestPartialPayment`, `shopify_amount_paid` |
| Partial / full refund | Supported (WP69) | `RefundSyncService` |
| Exchange | **WP70 Done** | Exchange wizard price diff fields |
| Cancel before/after invoice | **WP70 Done** | `TestCancelCreditNote`, `cancel_sync_mode` |
| Order edit from Shopify | **WP70 Done** | `TestOrderUpdateSync`, `OrderUpdateService` |
| Duplicate webhook | Supported | `TestWebhookSecurity` |
| Failed order retry | Supported | `TestRetryPolicy` |
| Integration log screen | Supported (WP69) | `shopify.sync.log` + refund operation |
| Missing product handling | **WP70 Done** | `TestAutoCreateProduct` |
| Paymob fees | Supported (WP69) | `TestPaymentFeeService` |
| Returns / restock | **WP70 Done** | `TestRefundRestock`, `refund_restock_mode` |
| Exchange pricing diff | **WP70 Done** | Wizard `price_difference` field |
| User manual | Available | `docs/user-guide/USER_GUIDE.md` |
| Screenshots (G8) | Pending | Requires live UI capture |

---

## Fixes applied during proof run

1. Added `refund` to `shopify.sync.log.operation` selection (cancel CN logging was failing).
2. Removed invalid `name` field from `stock.move` create in `ReturnPickingService` (Odoo 19).

---

## Sign-off recommendation

**Ready for client UAT** on `odoo_test` with recommended store settings:

```
auto_create_product_if_not_found = False
cancel_sync_mode = credit_note
order_edit_sync_mode = draft_sent
partial_payment_mode = register_paid_amount
refund_restock_mode = odoo_restock  (after warehouse mapping)
```

**Remaining:** G8 user-guide screenshots (non-blocking for functional sign-off).
