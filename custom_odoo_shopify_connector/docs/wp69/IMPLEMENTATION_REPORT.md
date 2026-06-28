# WP #69 Implementation Report

**Date:** 2026-06-28  
**Environment:** Odoo 19 Enterprise, DB `odoo_test`, module `custom_odoo_shopify_connector` **19.0.1.0.4**  
**Store:** iZone EG (`izone-eg.myshopify.com`)  
**Backup:** `/opt/odoo/backups/odoo_test_wp69_20260628_181311.dump`

---

## Summary

All 20 in-scope Excel requirements (excluding Amazon #21) were implemented on the Shopify connector. Module upgraded successfully; unit tests for payment fees passed (3/3).

---

## Traceability matrix (post-implementation)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| 1 | Sync Final Price Only | **Met** | `price_unit` = pre-discount; `shopify_original_price`, `shopify_line_discount_amount` on SO lines |
| 2 | Shipping as Line Item | **Met** | Existing `_create_shipping_lines()`; `delivery_product_id` exposed on store form |
| 3 | Payment Method Mapping | **Met** | `shopify_payment_gateway` persisted on SO at import |
| 4 | Conditional Fees Logic | **Met** | `PaymentFeeService` + gateway `fee_percent` / `fee_fixed` |
| 5 | Add Fees Line (Optional Mode) | **Met** | `fee_apply_mode`: line_item / invoice_discount / none |
| 6 | Return Detection | **Met** | Refund webhook + `sync_refund_from_order_payload` on fulfilled import |
| 7 | Auto Credit Note Creation | **Met** | Partial CN via `account.move` API; full reversal when no line items |
| 8 | Partial Return Handling | **Met** | `refund_line_items` mapped via `shopify_line_item_id` |
| 9 | Exchange Scenario Logic | **Met** | `shopify.exchange.wizard` → partial CN + replacement SO |
| 10 | Invoice & Credit Linking | **Met** | `reversed_entry_id`, credit note smart button on SO |
| 11 | Unified Order View | **Met** | Shopify notebook page + sync log / credit note stat buttons |
| 12 | Auto Sync Near Realtime | **Met** | Existing webhooks + cron (verified, no change required) |
| 13 | Historical Sync Logic | **Met** | `order_import_start_date` on store for first cron run |
| 14 | Transaction Logging | **Met** | Refund log type via `RefundSyncService._log` |
| 15 | Error Handling System | **Met** | Existing queue retry (verified) |
| 16 | Payment Net vs Gross | **Met** | `shopify_order_total`, `shopify_net_received`, `shopify_gateway_fee` |
| 17 | Refund Amount Logic | **Met** | Line-level amounts + shipping refund; outbound `refund_line_items` |
| 18 | Multi-Channel Journal Tagging | **Met** | Gateway `odoo_journal_id` with `ilike` match + fallback sync log |
| 19 | Delivered vs Invoiced Sync | **Met** | `shopify_fulfillment_status` (raw Shopify value) on import/webhook |
| 20 | Configurable Rules Engine | **Met** | Gateway fee fields + store `payment_fee_product_id`, `refund_sync_mode` |
| 21 | Amazon integration | **Out of scope** | Separate epic |

---

## UAT checklist (manual — odoo_test)

| Scenario | Expected | Verify in UI |
|----------|----------|--------------|
| Paymob order import | SO line at pre-discount price + 5% fee line | Configure Paymob gateway fee fields + fee product |
| COD order | No fee line | Gateway `fee_apply_mode = none` |
| Partial refund webhook | Partial credit note lines match refunded SKUs | Post invoice, trigger Shopify refund |
| Exchange wizard | Partial CN + new replacement SO linked | SO → Shopify Exchange |

---

## Key files added/changed

- `services/payment_fee_service.py` — fee computation and application
- `services/refund_sync_service.py` — partial/full refund sync
- `models/shopify_exchange_wizard.py` — exchange workflow
- `models/sale_order_line.py` — line item tracking fields
- Extended: `sale_order`, `shopify_payment_gateway`, `shopify_store`, `order_import_service`, `shopify_webhook`, views

---

## Tests

```
TestPaymentFeeService.test_paymob_fee_line_item — OK
TestPaymentFeeService.test_cod_zero_fee — OK
TestPaymentFeeService.test_invoice_discount_mode_stores_pending — OK
```

Run: `odoo-bin -d odoo_test --test-enable --test-tags=/custom_odoo_shopify_connector:TestPaymentFeeService`

---

## Admin configuration (iZone store)

1. **Store → Orders:** set `delivery_product_id`, `payment_fee_product_id`, optional `order_import_start_date`
2. **Payment Gateways → Paymob:** `fee_percent=5`, `fee_apply_mode=line_item`, `fee_product_id`, `odoo_journal_id`
3. **Payment Gateways → COD:** `fee_apply_mode=none`
4. **Refund sync mode:** `line_level` (default) or `full` for legacy full reversal

---

## Rollback

```bash
sudo systemctl stop odoo
sudo -u postgres pg_restore -d odoo_test --clean --if-exists /opt/odoo/backups/odoo_test_wp69_20260628_181311.dump
sudo systemctl start odoo
```
