# Shopify Connector — User Guide

**Module:** `custom_odoo_shopify_connector` (Odoo 19)  
**Audience:** Store administrators, accountants, and operations staff  
**Example store:** iZone EG (`izone-eg.myshopify.com`) on `odoo_test`

This guide explains how to use the Shopify connector day-to-day: setup, configuration, order accounting (fees, refunds, exchanges), product/customer/inventory sync, and troubleshooting. Screenshots were captured from the live test environment.

---

## Table of contents

1. [Accessing Odoo and the Shopify app](#1-accessing-odoo-and-the-shopify-app)
2. [Module overview](#2-module-overview)
3. [Initial setup — connect a Shopify store](#3-initial-setup--connect-a-shopify-store)
4. [Configuration reference](#4-configuration-reference)
5. [Use case scenarios](#5-use-case-scenarios)
6. [Menu map](#6-menu-map)
7. [Monitoring, queues, and logs](#7-monitoring-queues-and-logs)
8. [Troubleshooting](#8-troubleshooting)
9. [Appendix — field glossary](#9-appendix--field-glossary)

---

## 1. Accessing Odoo and the Shopify app

Open Odoo (Tailscale URL for iZone test env):

`https://worldposta-azone.tailcf9988.ts.net/odoo`

Log in with your database (e.g. `odoo_test`) and user credentials. From the home screen, click the **Shopify** app tile.

![Odoo home with Shopify app](images/01_odoo_home_shopify_app.png)

The Shopify app opens with a top menu: **Dashboard**, **Configuration**, **Products**, **Customers**, **Orders**, **Inventory**, **Shipping**, **Synchronization**, **Queues**, **Sync Logs**, **Reports**.

---

## 2. Module overview

The connector synchronizes data between Shopify and Odoo using three mechanisms:

| Mechanism | Purpose |
|-----------|---------|
| **Webhooks** | Near-real-time events (new orders, refunds, cancellations) |
| **Cron / schedulers** | Periodic fetch of orders, product queue processing, stock export |
| **Queues** | Background jobs with retry for products, orders, customers, inventory |

**Data flows:**

```mermaid
flowchart LR
  subgraph shopify [Shopify]
    SO[Orders]
    PR[Products]
    RF[Refunds]
  end
  subgraph odoo [Odoo]
    WH[Webhooks]
    Q[Queues]
    SALE[Sale Orders]
    INV[Invoices / Credit Notes]
  end
  SO --> WH --> Q --> SALE --> INV
  RF --> WH --> INV
  PR --> Q
```

**What Odoo stores per Shopify order:**

- Sale order with Shopify order ID and store link
- Payment gateway name, order total, gateway fee, net received
- Fulfillment status from Shopify
- Optional fee line, shipping line, partial refunds, exchange links

---

## 3. Initial setup — connect a Shopify store

### Step 1 — Create the store record

**Menu:** Shopify → Configuration → **Shopify Stores**

![Shopify store form — General tab](images/02_store_general.png)

| Field | What to enter |
|-------|----------------|
| **Name** | Display name (e.g. *iZone EG*) |
| **Shop URL** | `https://your-store.myshopify.com` |
| **Access Token** | Admin API token from Shopify custom app |
| **Webhook Secret** | Signing secret from Shopify webhooks |
| **Company** | Odoo company for this store |
| **Default Warehouse** | Warehouse for imported unshipped orders |
| **Sales Auto Workflow** | Default automation (confirm, invoice, payment) |

Click **Test Connection** on the store form to verify API access.

### Step 2 — Configure orders tab

Open the store → **Orders** tab.

![Store Orders tab — WP69 fields](images/03_store_orders_tab.png)

| Field | Recommended (iZone) |
|-------|---------------------|
| **Delivery Product** | Service product used for Shopify shipping lines |
| **Default Payment Fee Product** | Product for Paymob fee lines (e.g. *Payment Fees*) |
| **Order Import Start Date** | Cutoff for first historical import (avoids loading all history) |
| **Refund Sync Mode** | *Line-Level Partial Refunds* (default) or *Full Invoice Reversal* |
| **Import Orders With** | Usually *Unshipped* |
| **Manage Orders via Webhook** | Enable for real-time order import |

### Step 3 — Payment gateways

**Menu:** Shopify → Configuration → **Payment Gateways**

![Payment gateways list](images/04_payment_gateways_list.png)

Create one record per Shopify gateway string (must match `payment_gateway_names` from Shopify).

**Paymob example (5% + fixed fee):**

![Paymob gateway fee configuration](images/05_paymob_gateway_fees.png)

| Field | Paymob | COD |
|-------|--------|-----|
| **Payment Code** | `Paymob` (match Shopify exactly) | `Cash on Delivery (COD)` or `manual` |
| **Fee Apply Mode** | Add Line Item | None |
| **Fee Percent** | 5.00 | 0 |
| **Fixed Fee** | 2.00 | 0 |
| **Fee Product** | Payment Fees | — |
| **Payment Journal** | Bank / Paymob journal | COD journal |

### Step 4 — Order status / workflow mapping

**Menu:** Shopify → Configuration → **Order Status Mapping**

![Financial status mapping](images/11_financial_status_mapping.png)

Map each **payment gateway + financial status** (e.g. `paid`, `partially_paid`, `partially_refunded`) to a **Sales Auto Workflow** that controls confirm → invoice → register payment.

**WP70 store settings (Orders tab):**

| Field | Purpose |
|-------|---------|
| **Cancel Sync Mode** | Create credit note when Shopify cancels an invoiced order |
| **Order Edit Sync Mode** | Apply Shopify `orders/updated` edits to existing Odoo SOs |
| **Partial Payment Mode** | Register Shopify paid amount vs skip until fully paid |
| **Refund Restock Mode** | Create return pickings on Shopify refunds |
| **Outbound Refund Restock Type** | `restock_type` when pushing refunds to Shopify |

### Step 5 — Webhooks in Shopify Admin

In Shopify → Settings → Notifications → Webhooks, point to your Odoo base URL:

| Topic | Odoo endpoint |
|-------|----------------|
| Order creation | `/shopify/webhook/order` |
| Order cancelled | `/shopify/webhook/order_cancelled` |
| Refund created | `/shopify/webhook/refund_created` |

Copy the webhook signing secret into the store’s **Webhook Secret** field.

---

## 4. Configuration reference

### 4.1 Sales Auto Workflow

Defines automation after import:

1. Confirm quotation  
2. Create invoice  
3. Validate invoice  
4. Register payment (uses workflow journal, or gateway **Payment Journal** when matched)

### 4.2 Store — Products tab

- Auto-create product if not found  
- Sync product images  
- Shopify pricelist for export  

### 4.3 Store — Inventory tab

- Stock export type and interval  
- Default Shopify location for inventory sync  

### 4.4 Refund sync modes

| Mode | Behavior |
|------|----------|
| **Line-Level Partial Refunds** | Credit note lines match Shopify `refund_line_items` |
| **Full Invoice Reversal** | Legacy: full credit note via invoice reversal |

---

## 5. Use case scenarios

### UC-01 — First-time store connection

**Goal:** Verify Odoo can talk to Shopify before going live.

**Steps:**

1. Create store record with URL + access token.  
2. Click **Test Connection**.  
3. Open **Dashboard** — confirm store appears and queue counts load.

![Shopify dashboard](images/09_dashboard.png)

**Success:** No connection error; dashboard shows store metrics.

---

### UC-02 — Import a new Shopify order (COD, no fee)

**Goal:** Customer pays cash on delivery; no payment fee line.

**Preconditions:** COD gateway with **Fee Apply Mode = None**; webhooks or order cron active.

**Steps:**

1. Customer completes checkout on Shopify (COD).  
2. Webhook enqueues order → **Order Queue** processes it.  
3. Open **Shopify → Orders → Orders** — new sale order appears.

![Shopify sale orders list](images/06_shopify_orders_list.png)

4. Open the order → **Shopify** tab.

![Sale order Shopify tab](images/07_sale_order_shopify_tab.png)

**Verify:**

| Field | Expected |
|-------|----------|
| Shopify Payment Gateway | Cash on Delivery (COD) |
| Shopify Gateway Fee | 0.00 |
| Shopify Order Total | Matches Shopify checkout |
| Order lines | Products + shipping line (if configured) |

**Auto workflow:** Order confirmed, invoice posted, payment registered per COD journal mapping.

---

### UC-03 — Import a Paymob order (with 5% fee)

**Goal:** Online payment via Paymob; fee appears as separate line.

**Preconditions:** Paymob gateway configured (see §3 Step 3); **Fee Product** set on gateway and store.

**Steps:**

1. Customer pays via Paymob on Shopify.  
2. Order imports via webhook/cron.  
3. Open sale order — check lines:

   - Product lines at **pre-discount price** with discount % applied  
   - **Payment Fees** line = 5% of subtotal + fixed fee (e.g. 2.00 EGP)  
   - Shipping line (if applicable)

4. **Shopify** tab:

   | Field | Expected |
   |-------|----------|
   | Shopify Gateway Fee | Calculated fee amount |
   | Shopify Net Received | Order total − fee |

**Accounting:** Invoice includes fee line; payment registered on Paymob journal.

---

### UC-04 — Historical order import (first run)

**Goal:** Import orders from a start date without loading entire Shopify history.

**Steps:**

1. Store → **Orders** tab → set **Order Import Start Date** (e.g. 2026-01-01).  
2. Clear or leave empty **Last Order Import Time** only on first setup (cron advances it automatically after).  
3. Wait for **Import Orders** cron or click **Sync Orders** on the store.  
4. Monitor **Order Queue** for pending → done.

![Order queue](images/10_order_queue.png)

---

### UC-05 — Partial refund from Shopify (webhook)

**Goal:** Customer returns one item; partial credit note in Odoo.

**Preconditions:** Invoice posted; **Refund Sync Mode = Line-Level**; refund webhook configured.

**Steps:**

1. Process partial refund in Shopify Admin.  
2. Shopify sends `refunds/create` webhook.  
3. Odoo creates **partial credit note** linked to invoice (`reversed_entry_id`).  
4. On sale order: **Credit Notes** smart button shows count; **Shopify Refunded Amount** increases.  
5. Line-level **Shopify Refunded Qty** updated on affected SO lines.

**Verify:** Credit note lines match refunded SKUs and quantities — not a full invoice reversal.

---

### UC-06 — Product exchange (Odoo-initiated)

**Goal:** Customer exchanges an item; partial credit note + new replacement order.

**Steps:**

1. Open posted Shopify sale order (must have Shopify order ID).  
2. Click **Shopify Exchange**.  
3. In the wizard, confirm return qty and replacement product/qty per line.

![Shopify Exchange wizard](images/08_exchange_wizard.png)

4. Click **Process Exchange**.

**Result:**

- Partial credit note for returned lines  
- New sale order linked via **Exchange Parent Order**  
- Original order shows linked replacement under **Exchange Replacements**

---

### UC-07 — Cancel order in Shopify from Odoo

**Goal:** Odoo sale order cancelled → push cancel to Shopify.

**Steps:**

1. Cancel sale order in Odoo (standard flow).  
2. Click **Cancel on Shopify** (visible when order is cancelled in Odoo).  
3. Choose cancel reason and optional customer email.  
4. Confirm — sync log records API call.

---

### UC-08 — Push refund from Odoo to Shopify

**Goal:** Credit note created in Odoo → refund in Shopify.

**Steps:**

1. Create and post customer credit note linked to Shopify sale order.  
2. Ensure credit note is **paid** (reconciled).  
3. Open credit note → **Refund in Shopify** wizard.  
4. Connector sends `refund_line_items` when lines map to SO lines.

---

### UC-09 — Export inventory to Shopify

**Goal:** Odoo stock levels reflected in Shopify.

**Steps:**

1. Store → **Inventory** tab — enable stock export, set interval.  
2. Map warehouses/locations under **Configuration → Stock Settings**.  
3. Use **Synchronization → Export Stock** for manual run, or wait for cron.  
4. Monitor **Inventory Queue** under Queues.

---

### UC-10 — Import / sync products

**Goal:** Shopify catalog available as Odoo products.

**Steps:**

1. Store → click **Sync Products** (or use product queue).  
2. Monitor **Product Queue** — pending → processing → done.

![Product queue](images/13_product_queue.png)

3. Failed lines: open queue → **Retry Failed** after fixing root cause.  
4. Verify mappings under **Products → Product Mapping**.

---

### UC-11 — Push shipping / tracking to Shopify

**Goal:** When delivery is done in Odoo, fulfillment updates in Shopify.

**Steps:**

1. Store → **Orders** tab — enable **Update Order Shipping Status**.  
2. Complete delivery (validate picking with tracking).  
3. Cron pushes fulfillment to Shopify.  
4. Sale order **Shopify Fulfillment Status** updates (e.g. `fulfilled`).

---

### UC-12 — Monitor sync health

**Goal:** Investigate failed imports or API errors.

**Menu:** Shopify → Sync Logs

![Sync logs](images/12_sync_logs.png)

Filter by store, order ID, or status. For queue backlogs:

| Queue | Menu |
|-------|------|
| Orders | Queues → Order Queue |
| Products | Queues → Product Queue |
| Customers | Queues → Customer Queue |
| Inventory | Queues → Inventory Queue |

---

## 6. Menu map

| Menu | Purpose |
|------|---------|
| **Dashboard** | Store KPIs, queue summary |
| **Configuration → Shopify Stores** | Credentials, import/export settings |
| **Configuration → Payment Gateways** | Gateway codes, fees, journals |
| **Configuration → Order Status Mapping** | Financial status → workflow |
| **Configuration → Webhooks** | Registered webhook deliveries |
| **Products** | Odoo/Shopify products and mappings |
| **Customers** | Shopify-linked partners |
| **Orders → Orders** | Shopify sale orders |
| **Orders → Order Queue** | Pending order imports |
| **Orders → Refunds** | Credit notes / refunds |
| **Inventory** | Stock levels and export queues |
| **Shipping** | Deliveries, tracking export, carriers |
| **Synchronization** | Manual import/export wizards |
| **Queues** | All background job queues |
| **Sync Logs** | API and sync transaction log |
| **Reports** | Sales analysis, sync reports |

---

## 7. Monitoring, queues, and logs

### Queue statuses

| Status | Meaning |
|--------|---------|
| **Pending** | Waiting for worker |
| **Processing** | Currently importing |
| **Done** | Successfully completed |
| **Failed** | Error — check message, fix, retry |

### Sale order smart buttons (Shopify orders)

- **Sync Logs** — webhook/import history for this order  
- **Credit Notes** — linked refund documents  
- **Delivery / Invoices** — standard Odoo fulfillment and billing  

### Recommended daily checks

1. Order queue: failed count = 0  
2. Product queue: retry historical failures after fixes  
3. Sync logs: no repeated webhook HMAC failures  
4. Spot-check one Paymob and one COD order per week  

---

## 8. Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Orders not importing | Webhook secret mismatch / cron off | Verify webhook secret; check scheduled actions |
| Wrong workflow applied | Gateway name mismatch | Match **Payment Code** exactly to Shopify string |
| No fee line on Paymob order | Fee product empty or mode = None | Set gateway fee fields + fee product |
| Full CN on partial refund | Refund mode = Full | Set store **Refund Sync Mode** to Line-Level |
| Exchange wizard empty lines | Order imported before line IDs stored | Re-import or ensure `shopify_line_item_id` on lines |
| Product import failures | Duplicate variants / SKU conflicts | Retry failed queue; check product mapping |
| Cannot open Odoo URL | Need Tailscale VPN | Use `worldposta-azone.tailcf9988.ts.net` on tailnet |

---

## 9. Appendix — field glossary

### Sale order (Shopify tab)

| Field | Description |
|-------|-------------|
| Shopify Order ID | Shopify numeric order ID |
| Shopify Payment Gateway | Primary gateway from payload |
| Shopify Order Total | Gross total from Shopify |
| Shopify Gateway Fee | Computed payment fee |
| Shopify Net Received | Total minus gateway fee |
| Shopify Refunded Amount | Cumulative refunds synced |
| Shopify Fulfillment Status | Raw Shopify fulfillment value |
| Exchange Parent Order | Link to original order (replacement SOs) |

### Sale order line (technical)

| Field | Description |
|-------|-------------|
| shopify_line_item_id | Maps refunds to lines |
| shopify_original_price | Pre-discount unit price |
| shopify_line_discount_amount | Discount amount from Shopify |
| shopify_refunded_qty | Cumulative refunded quantity |

---

## Document info

| Item | Value |
|------|-------|
| Version | 1.0 |
| Date | 2026-06-28 |
| Module version | 19.0.1.0.4 |
| Screenshots | `docs/user-guide/images/` |
| E2E test (regenerate screenshots) | `e2e/` at repo root or `/opt/odoo-log-report-2026-06-28/e2e/` — run `npm test` and `node capture-extra-screenshots.mjs` |

For technical architecture and developer setup, see [README.md](../../README.md) and [WP69 implementation report](../wp69/IMPLEMENTATION_REPORT.md).
