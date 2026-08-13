# Paymob Fees — Technical Options Report (WP70)

**Module:** `custom_odoo_shopify_connector` `19.0.1.0.5`  
**Scope:** Documentation only. Default accounting behavior is **unchanged**.

## Current option (implemented)

Configured on `shopify.payment.gateway`:

| Setting | Meaning |
|---|---|
| `fee_percent` | Percentage fee |
| `fee_fixed` | Fixed fee |
| `fee_base` | `subtotal` or `order_total` |
| `fee_apply_mode` | `line_item` / `invoice_discount` / `none` |
| `fee_product_id` | Product used for fee line |

Behavior today:

1. Compute fee = `base * percent/100 + fixed`.
2. Store on SO: `shopify_gateway_fee`, `shopify_order_total`, `shopify_net_received` (gross − fee, or txn amount if present).
3. If `line_item`: create SO line with fee product at **positive** fee amount.
4. If `invoice_discount`: stash `shopify_fee_discount_pending` for invoice-time discount.

### Capability matrix (current code)

| Capability | Supported? | Notes |
|---|---|---|
| Percentage fee | Yes | `fee_percent` |
| Fixed fee | Yes | `fee_fixed` |
| Percentage + fixed | Yes | Summed in `compute_fee` |
| VAT on Paymob fee | No | No `fee_tax_id` application yet (field reserved) |
| Fee refund | No | Not reversed on Shopify refund webhook |
| Partial payment fees | Partial | Fee uses order total/subtotal, not paid portion |
| Multiple transactions / order | Partial | Paid amount sums sale txns; fee still once per order |

## Proposed accounting option (not activated)

Goal: clear receivable / bank / fee expense:

1. Customer payment recorded at **gross** (or contractual receivable amount).
2. Paymob fee posted to **expense account** (`fee_account_id`).
3. Bank/journal settlement at **net**.
4. Reconciliation: receivable ↔ payment; payment ↔ bank + fee expense.

### Suggested journal shape

```
Dr Bank                net
Dr Fee expense         fee (+ VAT if any)
   Cr Customer payment / receivable   gross
```

### Reserved settings (added, default-safe)

On `shopify.payment.gateway`:

- `fee_recording_mode` = `current` (default) | `expense_split` (reserved, **not executed**)
- `fee_account_id`
- `fee_tax_id`

Until finance signs off, keep `fee_recording_mode=current`.

## Recommendation

1. Finance to choose: keep fee-as-SO-line vs expense-split.
2. If expense-split: define `fee_account_id`, journal, VAT treatment, refunds of fees.
3. Only then implement posting logic behind `fee_recording_mode=expense_split`.

## Decision status

**OPEN** — no production accounting change in this remediation pass.
