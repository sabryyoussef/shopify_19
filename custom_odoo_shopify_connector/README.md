# Odoo Shopify Connector

[![Odoo 19.0](https://img.shields.io/badge/Odoo-19.0-875A7B?logo=odoo)](https://www.odoo.com)
[![License: LGPL-3](https://img.shields.io/badge/License-LGPL--3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)

**Seamless integration between Odoo ERP and Shopify.** Sync products, orders, customers, inventory, fulfillments, and refunds with queue-based processing and webhook support.

---

## Where to get it

| | |
|---|---|
| **Odoo Apps Store** | [Get it on the Odoo Apps Store](https://www.odoo.com/apps) — one-click install, updates, and official support. *Search for "Shopify Connector".* |
| **Source code (GitHub)** | [github.com/misri12/odoo_shopify_connector](https://github.com/misri12/odoo_shopify_connector) — clone, fork, or install from source (see [Install from GitHub](#42-install-from-github) below). |

---

## Table of contents

- [1. Overview](#1-overview)
- [2. Main Feature Set](#2-main-feature-set)
- [3. Architecture Overview](#3-architecture-overview)
- [4. Installation](#4-installation)
- [5. Configuration](#5-configuration)
- [6. Functional Workflows](#6-functional-workflows)
- [7. User Interface & Menus](#7-user-interface--menus)
- [8. Technical Details](#8-technical-details)
- [9. Performance & Scalability](#9-performance--scalability-considerations)
- [10. Limitations & Notes](#10-limitations--notes)
- [11. FAQ](#11-faq-examples)
- [12. Where to Go Next](#12-where-to-go-next)
- [User Guide (screenshots & use cases)](docs/user-guide/USER_GUIDE.md) — [plain text](docs/user-guide/USER_GUIDE.txt)
- [License & Support](#license--support)

---

## 1. Overview

The **Odoo Shopify Connector** creates a robust bridge between **Odoo ERP** and one or more **Shopify stores**.

It synchronizes:

- **Products & Variants**
- **Customers**
- **Orders**
- **Inventory**
- **Fulfillments / Shipping**
- **Refunds / Credit Notes**

All integrations are built to be:

- **Queue-based**: operations are queued and processed in the background.
- **Webhook-aware**: Shopify events (orders, refunds, customers) can be pushed into Odoo in near real time.
- **Multi-store capable**: support for multiple Shopify stores in one Odoo database.
- **Configurable**: mapping options for warehouses, locations, workflows, and payment/shipping.

---

## 2. Main Feature Set

### 2.1 Product Synchronization

- Export Odoo products to Shopify:
  - Simple products.
  - Variant products using Odoo attributes / variants.
- Synchronize:
  - **Title, description, SKU, barcode**.
  - **Prices**, potentially coming from Odoo pricelists.
  - **Images**, uploaded from Odoo product images to Shopify media.
- Update existing Shopify products when data changes in Odoo:
  - Prices.
  - Names.
  - Variant options.

### 2.2 Order Synchronization

- Automatically import **Shopify orders** as **Odoo Sale Orders**:
  - Order lines, quantities, prices and taxes.
  - Customer and delivery/billing addresses.
  - Shopify order ID is stored for traceability.
- Order status synchronization:
  - Shopify order status reflected in Odoo.
  - (Optionally) updates back to Shopify from Odoo, depending on configuration.
- Refund synchronization:
  - Odoo credit notes can be pushed to Shopify as refunds.

### 2.3 Customer Synchronization

- Import Shopify customers and create / update **Odoo partners**.
- Map:
  - Basic customer info (name, email, phone).
  - Invoice and delivery addresses.
  - Shopify customer IDs for future linking.
- Multi-store awareness:
  - Same person on multiple Shopify stores can be linked to the same Odoo partner, depending on your mapping rules.

### 2.4 Inventory Synchronization

- Export **on-hand stock** from Odoo to Shopify:
  - Per product / variant.
  - Per mapped Shopify location.
- Multi-warehouse support:
  - Map one or more Odoo warehouses / stock locations to Shopify locations.
  - Control which warehouses contribute to the published stock.
- Automatic updates:
  - Stock changes coming from:
    - Incoming shipments.
    - Outgoing deliveries.
    - Returns.
  - Can be pushed to Shopify via queue jobs / schedulers.

### 2.5 Fulfillment / Shipping

- From Odoo deliveries:
  - Send **tracking numbers** to Shopify.
  - Update fulfillment status on the Shopify order.
- Optional controls:
  - Decide which operations (e.g. specific picking types) should create fulfillments in Shopify.
  - Control partial vs. complete fulfillment logic (depending on your flow).

### 2.6 Multi-Store Support

- Manage multiple Shopify stores from one Odoo database:
  - Each store has its own credentials and URL.
  - Separate configuration per store (warehouses, pricelists, financial status mapping, etc.).
- Orders, products and inventory are always linked to the correct store.

### 2.7 Queue System & Technical Hardening

- Every sync operation (product export, order import, inventory update, etc.) is stored in **queues**:
  - Clear statuses: Pending, Processing, Done, Failed.
  - Error message fields for failed jobs.
- Cron jobs process queues in the background.
- Manual actions:
  - Retry failed jobs.
  - Cancel jobs that should not be processed.

---

## 3. Architecture Overview

At a high level, the module is organized into the following parts:

- **Addons included in this repository**
  - **Main connector addon (root)**: `__manifest__.py`, `models/`, `services/`, `mappers/`, `controllers/`, `views/`, `data/`, `security/`, `static/`
  - **Product Data Custom addon**: `product_data_custom/` (additional product content/SEO/custom fields + supporting models + menus)

- **Models**:
  - Shopify store configuration.
  - Queues (product, order, inventory, price updates, etc.).
  - Mapping/helper models for products, orders, and other Shopify entities.
- **Services**:
  - Encapsulate the HTTP communication with Shopify (REST Admin API).
  - Handle pagination, authentication, rate limiting behavior and error handling.
- **Mappers**:
  - Convert Shopify JSON payloads into Odoo values and vice versa.
  - Separate mapping logic from business logic.
- **Webhooks**:
  - Endpoints for Shopify events (e.g. `orders/create`).
  - Verify Shopify signatures for security.
  - Convert incoming payloads into queue records.
- **Queues**:
  - Store data to be processed asynchronously (e.g. `product_queue`, `order_queue`).
  - Contain status, error messages, and references to target Odoo records.
- **Schedulers (cron jobs)**:
  - Process queue records in the background.
  - Trigger regular exports/imports (e.g. stock export every X minutes).

This layered design ensures:

- Clean separation between:
  - Data transport (API / webhooks).
  - Business rules (services).
  - Data mapping (mappers).
  - Asynchronous processing (queues & cron).
- Easy maintenance and debugging, since you can inspect queue records, webhooks and logs.

---

## 4. Installation

### 4.1 Requirements

- **Odoo 19.0** (module is designed for this version).
- Core Odoo apps and dependencies:
  - `base`, `product`, `stock`, `sale_management`, `account`,
  - `website_sale`, `delivery`, `stock_delivery`.
- External Python dependency:
  - `openpyxl` (declared in `__manifest__.py` → `external_dependencies`).

### 4.2 Steps

1. Copy the module into your Odoo addons path:

   - Example:  
     `odoo-19.0/custom_addons/odoo_shopify_connector`

2. Update the Apps list in Odoo:

   - Go to **Apps > Update Apps List** (or enable developer mode and click “Update Apps List”).

3. Search for **“Shopify Connector”** in Apps.

4. Click **Install**.

5. Ensure that any required dependencies (Python packages, Odoo core modules) are installed and loaded correctly.

### 4.3 Install from GitHub (source)

1. Clone the repository into your Odoo addons path:

   ```bash
   cd /path/to/odoo/addons   # or your custom_addons folder
   git clone https://github.com/gultajkhan/odoo_shopify_connector.git
   ```

2. Restart Odoo and **Update Apps List** (**Apps** → **Update Apps List**).

3. Search for **"Shopify Connector"** and click **Install**.

4. Ensure the Python dependency is installed:

   ```bash
   pip install openpyxl
   ```

   *Replace the clone URL with your actual GitHub repository URL.*

---

## 5. Configuration

### 5.1 Shopify Store Setup in Odoo

1. **Create a Shopify Store record**:
   - Go to:  
     `Sales ▸ Configuration ▸ Shopify Stores` (menu may vary slightly depending on your menus).
   - Click **Create**.
   - Set fields such as:
     - `Shop URL` (e.g. `your-shop.myshopify.com`).
     - `Access Token` (Admin API access token).
     - Company, currency, and other defaults.

2. **API Credentials**:
   - In your Shopify admin:
     - Create a **Custom app** (if using private/custom apps) or use an existing app’s Admin API credentials.
     - Generate the **Admin API access token** with permissions for:
       - Products, Inventory.
       - Orders, Customers.
       - Fulfillment, Refunds.
   - Copy the token into your Shopify Store record in Odoo:
     - `Access Token` field (and other fields if present, such as API key/secret).

3. **Warehouse & Location Mapping**:
   - In Odoo, configure which **warehouse** and/or stock locations correspond to each Shopify location.
   - This is used when exporting inventory to Shopify.

4. **Pricelist & Financial Settings**:
   - Optionally, set which Odoo **Pricelist** should be used for product prices sent to Shopify.
   - Configure mapping for:
     - Payment terms.
     - Financial status.
     - Auto-confirmation of orders (if supported by your module).

5. **Test Connection**:
   - From the Shopify Store form, click **Test Connection** (if a button is provided).
   - Ensure that Odoo can successfully talk to Shopify.

### 5.2 Webhook Configuration in Shopify

To receive real-time orders and other events:

1. In Shopify admin, go to **Settings ▸ Notifications ▸ Webhooks** (or **Settings ▸ Apps & sales channels** depending on your version).
2. Create a webhook for:
   - **Orders → orders/create**  
     URL: `https://YOUR_ODOO_DOMAIN/shopify/webhook/order`
   - Optionally other events:
     - `orders/updated`
     - `refunds/create`
     - `customers/create` / `customers/update`
3. Copy the **webhook secret** Shopify provides and paste it into the Odoo Shopify Store’s **Webhook Secret** field.
4. Shopify will send events to Odoo:
   - The connector verifies the signature.
   - If valid, it stores payloads in queue models for safe background processing.

### 5.3 Cron / Scheduler Configuration

The module usually creates one or more **cron jobs** to process queues and run periodic syncs, e.g.:

- Product export queue processor.
- Price update queue processor.
- Order queue processor.
- Inventory export scheduler.

In Odoo:

1. Go to **Settings ▸ Technical ▸ Automation ▸ Scheduled Actions** (developer mode).
2. Locate actions related to Shopify (e.g. “Process Shopify Order Queue”, “Export Shopify Inventory”).
3. Ensure:
   - They are **active**.
   - Intervals are suitable (e.g. every 1–5 minutes for orders, every 10–30 minutes for inventory, depending on volume).

---

## 6. Functional Workflows

### 6.1 Product Sync Workflow

**Flow:**  
`Odoo Product → Connector API / Queue → Shopify Product`

1. You create or edit a product in Odoo:
   - Name, SKU, prices, images, variants.
2. The connector:
   - Creates a **product export queue job** for that product.
3. The cron job processes the queue:
   - Reads product data from Odoo.
   - Maps it to the Shopify product schema (mappers).
   - Sends the payload via the Shopify Admin API.
4. Shopify:
   - Creates or updates the product.
   - Publishes it to the relevant sales channels (depending on your Shopify settings).

### 6.2 Order Import Workflow

**Flow:**  
`Shopify Order → Webhook → Queue System → Odoo Sale Order`

1. A customer checks out in Shopify and an order is created.
2. Shopify sends an `orders/create` webhook to:
   - `https://YOUR_ODOO_DOMAIN/shopify/webhook/order`
3. Odoo webhook controller:
   - Verifies the signature.
   - Creates a **Shopify order queue** entry with status **Pending**.
4. A cron job processes the queue:
   - Maps customer and addresses to an Odoo partner.
   - Creates a **Sale Order** with order lines, taxes, and totals.
   - Optionally confirms the sale order, generating deliveries and invoicing flows depending on settings.
5. You can monitor this flow via:
   - Shopify Order Queue menu in Odoo.
   - Logs or chatter on the created Sales Orders.

### 6.3 Stock Sync Workflow

**Flow:**  
`Odoo Warehouse → Stock Export Engine → Shopify Inventory`

1. Stock levels change in Odoo due to:
   - Incoming shipments.
   - Outgoing deliveries.
   - Returns, adjustments.
2. The connector:
   - Calculates on-hand quantities per product/variant and per mapped location.
   - Adds inventory export jobs to the **inventory queue**.
3. The inventory export cron:
   - Pushes updated quantities to Shopify locations.
4. Shopify:
   - Updates inventory records and prevents overselling.

### 6.4 Refund Sync Workflow

**Flow:**  
`Odoo Credit Note → Refund API → Shopify Refund`

1. In Odoo, you create a **credit note** from the original customer invoice or via the Shopify-specific refund wizard (if present).
2. The connector:
   - Maps the credit note information to a Shopify refund payload.
   - Sends it to Shopify through the refund API.
3. Shopify:
   - Registers the refund against the original order.
   - Triggers any customer notifications configured in Shopify.

---

## 7. User Interface & Menus

The exact labels may vary slightly, but you can expect menus such as:

- **Shopify ▸ Configuration**
  - Shopify Stores.
  - Locations / Warehouse Mapping.
  - Payment / Financial Status settings.
- **Shopify ▸ Queues**
  - Order Queue.
  - Product Queue.
  - Inventory / Stock Queue.
  - Price Update Queue (if available).
- **Shopify ▸ Operations**
  - Product export actions (from products or dedicated wizards).
  - Order import / re-sync actions.
- **Shopify ▸ Dashboards** (if you have dashboard views included).

The visual offline documentation at `static/description/index.html` can be used as a guided tour for end-users.

---

## 8. Technical Details

### 8.1 Queues

Typical queue records include fields like:

- Reference to Shopify store.
- Reference to the Odoo record (product, order, etc.).
- JSON payload (raw Shopify data or prepared payload).
- Status: `pending`, `processing`, `done`, `failed`.
- Retry count and error messages.

Best practices:

- Use queues as your **first place to look** when something is not synchronized.
- If a record is stuck in `failed`:
  - Read the error message.
  - Fix configuration / data.
  - Use the **retry** action if available.

### 8.2 Custom Odoo fields (Connector core)

This addon adds “technical linking” fields on standard Odoo models so Shopify ↔ Odoo sync can be **idempotent**, **multi-store aware**, and **auditable**.

**Shopify Store (`shopify.store`)**

- **Connection & security**
  - `shop_url`, `access_token`, `api_key`, `api_secret`, `webhook_secret`, `active`
- **Inventory defaults**
  - `shopify_location_id` (default Shopify location id used by inventory sync)
- **Company / operational defaults**
  - `company_id`
  - `default_unshipped_order_warehouse_id`
  - `sale_auto_workflow_id`
  - `delivery_product_id`
- **Order import behavior**
  - `import_order_status`, `use_odoo_sequence`, `order_prefix`
  - `default_pos_customer_id`, `auto_fulfill_gift_card`
  - `shopify_tax_behavior`, `manage_orders_webhook`
  - `last_order_import_time`
- **Schedulers (enable + interval + checkpoints)**
  - Shipping status → Shopify: `update_shipping_sync_enabled`, `update_shipping_interval_number`, `update_shipping_interval_type`, `last_shipping_sync_time`
  - Import shipped orders: `import_shipped_orders_enabled`, `import_shipped_orders_interval_number`, `import_shipped_orders_interval_type`, `last_shipped_orders_import_time`
  - Automatic stock export: `export_stock_enabled`, `export_stock_interval_number`, `export_stock_interval_type`, `last_stock_export_time`
- **Product import / export preferences**
  - `auto_create_product_if_not_found`, `sync_product_images`
  - `import_sales_description`, `use_odoo_description_on_export`
  - Pricing: `pricelist_id` (legacy), `shopify_pricelist_id` (preferred)
  - Stock compute: `shopify_stock_type` (`free_qty` or `forecast_qty`)

**Product Template (`product.template`)**

- `shopify_product_id`: primary Shopify product id (main store)
- `shopify_product_map_ids`: per-store mapping records (`shopify.product.map`)
- `*_prepare_shopify_metafields()`: optional “full metadata export” to Shopify metafields (see **8.5**)

**Product Variant (`product.product`)**

- `shopify_product_id`, `shopify_variant_id`: primary Shopify ids (main store)
- `shopify_variant_map_ids`: per-store variant mappings (`shopify.variant.map`)
- Stock export override:
  - `shopify_stock_type` (`fixed` / `percentage`)
  - `shopify_stock_value`

**Customer (`res.partner`)**

- `shopify_customer_id`: links an Odoo partner to a Shopify customer id

**Sales Order (`sale.order`)**

- `shopify_order_id`, `shopify_instance_id`
- Fulfillment/shipping push tracking:
  - `shopify_fulfillment_status` (`pending` / `fulfilled` / `failed`)
  - `shopify_fulfilled` (computed boolean)
- Cancellation/refund flags:
  - `shopify_cancelled`, `shopify_cancel_reason`
  - `shopify_refunded`, `shopify_refund_date`

**Delivery (`stock.picking`)**

- Tracking fields
  - `carrier_tracking_ref` (indexed)
  - `shopify_tracking_reference` (alias / related)
- Shipping sync state
  - `shopify_shipping_status` (`pending` / `done` / `failed`)
  - `shopify_fulfilled` (computed boolean)
  - `shopify_has_tracking` (computed boolean; also checks package tracking)

### 8.3 Custom product content fields (Addon: `product_data_custom`)

The `product_data_custom` addon extends `product.template` with rich content, merchandising and SEO fields used by the connector’s metafield sync.

**Product Template (`product.template`) content & SEO**

- **Taxonomy**
  - `brand_id` → `product.brand`
  - `type_id` → `product.type`
  - `collection_ids` → `product.collection` (m2m)
  - `hair_type_ids` → `hair.type` (m2m)
- **Rich content**
  - `benefits` (HTML)
  - `description_long` (HTML)
  - `application` (HTML)
  - `ingredients` (HTML)
  - `inci_list` (HTML/text)
- **Merchandising**
  - `cross_sell_ids` (m2m to `product.template`)
  - `similar_product_ids` (m2m to `product.template`)
- **SEO**
  - `meta_title` (Char)
  - `meta_description` (Text)
- **Content blocks**
  - `block1_title`, `block1_text`, `block1_image`
  - `block2_title`, `block2_text`, `block2_image`

**Shopify-oriented aliases on `product.template` (technical fields)**

These fields exist in `product_data_custom` and are intended as Shopify-side representations / references:

- `benefices`, `inci_liste`
- `bloc_titre_1`, `bloc_texte_1`, `bloc_image_1`
- `bloc_titre_2`, `bloc_texte_2`, `bloc_image_2`

**Additional models created by `product_data_custom`**

- `product.brand` (unique by `lower(name)`)
- `product.type` (unique by `lower(name)`)
- `product.collection` (unique by `lower(name)`)
- `hair.type` (unique by `lower(name)`)
- `product.content.block`: reusable blocks
  - `product_id`, `title`, `content`, `image`, `sequence`

### 8.4 Shopify product metafields sync (curated: `odoo_custom`)

The connector supports importing and exporting **Shopify product metafields** under the namespace:

- `odoo_custom`

These metafields are used to synchronize optional “extra” product fields (when the corresponding Odoo fields exist in your database).

**Conflict handling / timestamps**

- For each metafield key `X`, the exporter also writes a companion timestamp metafield `ts_X` (type `date_time`).
- During import, the connector uses these timestamps to decide whether to apply the Shopify-side value (last-updated-wins).

**Supported keys (namespace `odoo_custom`)**

- **Sync marker**
  - `odoo_write_date` (type `date_time`)
- **Single line text**
  - `brand`
  - `product_type_custom`
  - `meta_title`
  - `meta_description`
  - `block1_title`
  - `block2_title`
- **Multi line text**
  - `benefits`
  - `description_long`
  - `application`
  - `ingredients`
  - `inci_list`
  - `block1_text`
  - `block2_text`
  - `block1_image_b64` (base64 text)
  - `block2_image_b64` (base64 text)
- **JSON**
  - `collections` (list of names)
  - `hair_types` (list of names)
  - `cross_sell` (list of `{id, name}`)
  - `similar_products` (list of `{id, name}`)

**Timestamps written for conflict resolution**

- For each metafield key `X` exported, the connector can also write `ts_X` (type `date_time`) using the Odoo product’s `write_date`.
- On import, “last-updated-wins” can be implemented using those timestamps.

### 8.5 Shopify product metafields sync (full metadata export: `custom`)

In addition to the curated `odoo_custom` keys, the connector includes an optional “full metadata export” method on `product.template`:

- **Namespace**: `custom`
- **Keys**: the **technical Odoo field names** from `product.template` (e.g. `name`, `default_code`, `meta_title`, etc.)
- **Type rules (export)**
  - Scalars (string/number/bool) → `single_line_text_field`
  - `dict`/`list` and some recordset `ids` export paths → `json`
  - File references (`gid://...`) → `file_reference`
  - **Skipped**: binary/image fields and most recordsets (not JSON-serializable)
  - Oversized strings (> 65,536 chars) are skipped (Shopify limit)

This is useful when you want Shopify to store additional product metadata (for themes, headless storefronts, search indices, etc.) without writing one-off mapping code for each field.

### 8.6 Concurrency & Transaction Safety (Product Import Queue)

When importing products, PostgreSQL can sometimes raise:

- `could not serialize access due to concurrent update`

This happens when multiple workers/transactions update the same product/variant rows at the same time (e.g., concurrent queue processing).

To keep queue processing reliable:

- Queue line claiming is committed before heavy processing starts.
- Each queue line is processed in its own transaction (commit per line).
- On a transaction abort, the connector rolls back and then records the queue line failure cleanly.
- Serialization conflicts are retried a limited number of times.

### 8.4 Cron Jobs

- Cron jobs are responsible for:
  - Picking up `pending` queue records.
  - Marking them as `processing`.
  - Executing the relevant service logic.
  - Updating statuses to `done` or `failed`.
- If you have high volumes:
  - You can increase the frequency or batch size.
  - Make sure your Odoo workers / server resources can handle the load.

### 8.3 Webhooks

- All webhook endpoints:
  - Validate Shopify signatures using the shared secret.
  - Reject requests with invalid signatures to avoid malicious calls.
  - Create queue entries instead of doing heavy work in the HTTP request.
- If you don’t want realtime imports:
  - You can disable webhooks and rely on scheduled pull-based imports (if implemented).

### 8.6 Error Handling & Logging

- When a job fails:
  - The queue record gets `failed` status and an error message.
  - Depending on your configuration, the system might:
    - Attempt a **limited number of retries**.
    - Leave the job for manual review.
- For deeper diagnostics:
  - Check **Odoo logs** (server logs) for stack traces or low-level API errors.
  - Look at relevant records (e.g. product, order) for chatter messages if your implementation logs there.

---

## 9. Performance & Scalability Considerations

- Use **queues and cron jobs** rather than synchronizing in real time from UI actions.
- Set reasonable intervals:
  - Orders: every 1–5 minutes (or rely primarily on webhooks).
  - Inventory: every 10–30 minutes depending on how fast you sell out.
  - Products/prices: every 15–60 minutes or only on demand.
- For very large catalogs or high order volume:
  - Consider vertical scaling (more workers, more RAM) or horizontal scaling of your Odoo instance.
  - Tune batch sizes and cron intervals.

---

## 10. Limitations & Notes

- The connector expects **clean and consistent data**:
  - Product codes/SKUs.
  - Taxes and fiscal positions.
  - Warehouses and locations.
- Some Shopify / Odoo edge cases (complex discounts, unusual tax rules, custom apps) may require customization.
- Multi-currency and multi-company setups:
  - Supported to the extent Odoo supports them, but may require additional configuration or customizations depending on your accounting requirements.

---

## 11. FAQ (Examples)

**Q: Can I run multiple Shopify stores on one Odoo?**  
**A:** Yes. Each store is configured separately with its own credentials and mapping rules.

**Q: Where can I see if an order import failed?**  
**A:** In the **Shopify Order Queue** menu in Odoo. Look for records with status `failed` and read the error message.

**Q: How do I stop overselling?**  
**A:** Ensure:
- Inventory export cron is active and runs frequently.
- Warehouses and Shopify locations are mapped correctly.
- Only correct stock locations are used for export.

**Q: Can I customize the mapping (for example, how payment terms or shipping methods are set)?**  
**A:** Yes. The module uses mappers and configuration models that can be extended (via Odoo inheritance) to adjust mapping logic.

---

## 12. Where to Go Next

- **For end users / demo**:  
  Open the offline visual page at `static/description/index.html` in a browser to see diagrams, charts, and animated workflows.

- **For implementers**:
  - Review **Shopify Stores** configuration and queues.
  - Test flows with a staging Shopify store before going live.

- **For developers**:
  - Explore models related to:
    - Stores, queues, and webhooks.
    - Product/order mappers and services.
  - Extend or override them as needed to implement project-specific logic.

---

## License & Support

- **License:** [LGPL-3](https://www.gnu.org/licenses/lgpl-3.0). You may use, modify, and distribute this module under the terms of the GNU Lesser General Public License v3.
- **Author / Maintainer:** Gultaj Khan (`@misri12`)  
- **Email:** `gultajkhan980@gmail.com`, `misrikhan394@gmail.com`  
- **Phone (WhatsApp / calls):** `+92 315 1945928`  
- **GitHub:** [https://github.com/misri12](https://github.com/misri12)  

For GitHub users, open an issue in the repository for bugs or feature requests, or contact me directly using the details above.

