# Shopify ↔ Odoo UAT Checklist (WP70)

**Environment:** Test DB only (`odoo_test`)  
**Module:** `custom_odoo_shopify_connector` 19.0.1.0.5  
**Rule:** Do not mark Pass if skipped without a documented reason.

For each scenario fill: Actual result / Evidence (SO id, CN id, log id, screenshot) / Pass/Fail.

Legend status column: `Ready` | `Blocked` | `Passed` | `Not Run`

---

## Shared template (copy per run)

| Field | Value |
|---|---|
| Preconditions | |
| Shopify steps | |
| Expected Odoo result | |
| Expected accounting result | |
| Expected stock result | |
| Expected webhook/log result | |
| Idempotency retry step | |
| Actual result | |
| Evidence | |
| Pass/Fail | |

---

## 1. بدون خصم — Status: Ready

- **Preconditions:** Mapped product; store webhook enabled.
- **Shopify steps:** Create paid order, no discounts.
- **Expected Odoo:** SO lines discount=0; totals match Shopify.
- **Accounting:** Invoice = order total; no fee/discount surprises.
- **Stock:** Standard delivery reservation.
- **Webhook/log:** orders/create success; no duplicate SO on retry.
- **Idempotency retry:** Resend same webhook_id → duplicate ignored.

## 2. Coupon — Status: Ready

- **Preconditions:** Coupon code active in Shopify.
- **Shopify steps:** Apply coupon; place order.
- **Expected Odoo:** Line discount % or amount; `shopify_discount_source` contains `coupon`.
- **Accounting:** Untaxed/tax reflect discounted base.
- **Stock:** Unchanged vs non-discount.
- **Webhook/log:** Discount source logged/stored.
- **Idempotency retry:** No doubled discount.

## 3. Automatic discount — Status: Ready

- Same as #2 with automatic app; `shopify_discount_source` contains `automatic`.

## 4. أكثر من منتج — Status: Ready

- Multi line_items; mapping by variant/SKU; totals = sum of lines + shipping − discounts.

## 5. أكثر من شحنة — Status: Ready

- Multiple fulfillments / shipping lines; no duplicate delivery lines; fulfillment ids stored.

## 6. Free shipping — Status: Ready

- Shipping line price 0; delivery product line price 0; no extra fee lines.

## 7. Partial payment — Status: Ready

- Transactions partial; paid amount from sale txns; payments keyed by `shopify_transaction_id`.

## 8. Partial refund — Status: Ready

- Refund webhook; partial CN; restock per `refund_restock_mode`; idempotent on refund id.

## 9. Full refund — Status: Ready

- Full CN / reverse; stock return if configured; second webhook no second CN.

## 10. Exchange أغلى — Status: Ready

- Wizard: CN + replacement SO + balance-due line; not auto-paid; `shopify_exchange_key` set.

## 11. Exchange أرخص — Status: Ready

- CN includes extra credit amount; replacement SO without unexplained negative lines; diff negative.

## 12. Cancel قبل الشحن — Status: Ready

- Cancel before posted invoice: SO cancel, open pickings cancel, draft invoices cancel, **no CN**.

## 13. Cancel بعد posted invoice — Status: Ready

- CN via `cancel-<order_id>`; SO cancelled; idempotent.

## 14. Order edit قبل التأكيد — Status: Ready

- Draft/sent SO updated per `order_edit_sync_mode`.

## 15. Order edit بعد التأكيد — Status: Ready / Blocked if mode forbids

- Confirm expected mode (`draft_sent` / etc.); verify no silent corruption.

## 16. Duplicate webhook — Status: Ready

- Same `X-Shopify-Webhook-Id` → one event; no second SO/payment/CN.

## 17. Failed webhook ثم Replay — Status: Ready

- Event status failed → Replay; attempt++, last_attempt_at; processing→processed/failed; same webhook_id/order_id; no new SO if already imported.

## 18. Missing product auto-create=False — Status: Ready

- ValidationError / failed queue; no silent skip without log.

## 19. Missing product auto-create=True — Status: Ready

- Product created; variant map created when ids present.

## 20. Mapping via Shopify Variant ID — Status: Ready

- `shopify.variant.map` hit before SKU.

## 21. Mapping via SKU — Status: Ready

- Fallback `default_code` when variant map missing.

## 22. Paymob fee — Status: Ready (technical) / Blocked (accounting decision)

- **Preconditions:** Gateway fee_percent/fixed configured; mode line_item or invoice_discount.
- **Expected Odoo:** Fee amount stored; line or pending discount.
- **Accounting:** **Pending finance decision** — see `PAYMOB_FEES_ACCOUNTING_OPTIONS.md`. Do not assert expense journals until approved.
- **Idempotency:** Re-import does not double fee line (manual UAT check).

---

## Execution tracker

| # | Scenario | Status |
|---|---|---|
| 1 | بدون خصم | Not Run |
| 2 | Coupon | Not Run |
| 3 | Automatic discount | Not Run |
| 4 | أكثر من منتج | Not Run |
| 5 | أكثر من شحنة | Not Run |
| 6 | Free shipping | Not Run |
| 7 | Partial payment | Not Run |
| 8 | Partial refund | Not Run |
| 9 | Full refund | Not Run |
| 10 | Exchange أغلى | Not Run |
| 11 | Exchange أرخص | Not Run |
| 12 | Cancel قبل الشحن | Not Run |
| 13 | Cancel بعد posted invoice | Not Run |
| 14 | Order edit قبل التأكيد | Not Run |
| 15 | Order edit بعد التأكيد | Not Run |
| 16 | Duplicate webhook | Not Run |
| 17 | Failed webhook ثم Replay | Not Run |
| 18 | Missing product auto-create=False | Not Run |
| 19 | Missing product auto-create=True | Not Run |
| 20 | Mapping Variant ID | Not Run |
| 21 | Mapping SKU | Not Run |
| 22 | Paymob fee | Not Run |
