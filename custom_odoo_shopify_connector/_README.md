# Odoo Shopify Connector - Complete Client Documentation

[![Odoo 19.0](https://img.shields.io/badge/Odoo-19.0-875A7B?logo=odoo)](https://www.odoo.com)
[![License: LGPL-3](https://img.shields.io/badge/License-LGPL--3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)

**Enterprise-grade bidirectional integration between Odoo ERP and Shopify.**

---

## Executive Summary

The Odoo Shopify Connector enables seamless synchronization between Odoo ERP and Shopify e-commerce platforms. Built with enterprise architecture patterns, it supports multi-store configurations, queue-based processing, webhook integration, and comprehensive error handling.

### Key Capabilities
- **Bidirectional Sync**: Products, orders, customers, inventory, fulfillments, refunds
- **Multi-Store Support**: Manage multiple Shopify stores from single Odoo instance
- **Queue-Based Architecture**: Reliable background processing with retry mechanisms
- **Real-Time Webhooks**: Near-instant order and customer imports
- **Enterprise Security**: HMAC signature verification, HTTPS enforcement

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      ODOO ERP INSTANCE                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │   Models     │  │   Services   │  │   Mappers    │           │
│  │ • Store      │  │ • ShopifyAPI │  │ • Product    │           │
│  │ • Queues     │  │ • ProductSvc │  │ • Order      │           │
│  │ • Mappings   │  │ • OrderSvc   │  │ • Customer   │           │
│  │ • Sync Logs  │  │ • Inventory  │  │ • Inventory  │           │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │
│         └─────────────────┴─────────────────┘                  │
│                              │                                  │
│              ┌───────────────▼───────────────┐                  │
│              │  Queue Processing Engine       │                  │
│              │  (Cron Jobs & Schedulers)     │                  │
│              └───────────────┬───────────────┘                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
    ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
    │ SHOPIFY      │   │ SHOPIFY      │   │ SHOPIFY      │
    │ STORE 1      │   │ STORE 2      │   │ STORE N      │
    └──────────────┘   └──────────────┘   └──────────────┘
```

---

## Data Flow Diagrams

### Product Export Flow (Odoo → Shopify)

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   User      │───▶│   Export    │───▶│   Product   │───▶│   Shopify   │
│  Action     │    │   Wizard    │    │   Service   │    │    API      │
└─────────────┘    └─────────────┘    └──────┬──────┘    └─────────────┘
                                              │
                       ┌──────────────────────┼──────────────────────┐
                       ▼                      ▼                      ▼
                ┌─────────────┐       ┌─────────────┐       ┌─────────────┐
                │    Map      │       │   Create/   │       │   Update    │
                │   Product   │──────▶│   Update    │──────▶│   Mapping   │
                │   Data      │       │   Product   │       │   Tables    │
                └─────────────┘       └─────────────┘       └─────────────┘
```

### Order Import Flow (Shopify → Odoo)

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Shopify   │───▶│   Webhook   │───▶│   Order     │───▶│   Create    │
│   Order     │    │   Handler   │    │   Queue     │    │   Sale      │
│   Created   │    │             │    │             │    │   Order     │
└─────────────┘    └─────────────┘    └──────┬──────┘    └─────────────┘
                                             │
                                             ▼
                                      ┌─────────────┐
                                      │   Map       │
                                      │   Customer  │
                                      │   & Lines   │
                                      └─────────────┘
```

### Inventory Sync Flow

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Stock     │───▶│   Stock     │───▶│   Shopify   │───▶│   Shopify   │
│   Change    │    │   Service   │    │    API      │    │   Location  │
│  in Odoo    │    │             │    │             │    │   Update    │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

---

## Component Overview

### Models (Data Layer)

| Component | Purpose | Key Fields |
|-----------|---------|------------|
| `shopify.store` | Store configuration | URL, API token, webhook secret |
| `shopify.product.layer` | Export staging | Product mapping, overrides |
| `shopify.product.map` | ID mapping | Shopify ID ↔ Odoo ID |
| `shopify.order.queue` | Order processing queue | Payload, status, retry count |
| `shopify.import.queue` | Import job queue | Store, model, filters |
| `shopify.stock.queue` | Stock sync queue | Product, location, quantity |

### Services (Business Logic)

| Component | Purpose | Key Methods |
|-----------|---------|-------------|
| `ShopifyAPI` | REST API client | `_request`, `create_product`, `update_product` |
| `ProductService` | Product export | `export_product_to_shopify`, `_compute_checksum` |
| `OrderService` | Order import | `import_order`, `create_sale_order` |
| `InventoryService` | Stock sync | `export_stock`, `update_inventory` |

### Mappers (Data Transformation)

| Component | Purpose |
|-----------|---------|
| `ProductMapper` | Odoo Product → Shopify JSON |
| `OrderMapper` | Shopify Order → Odoo Sale Order |

---

## Process Workflows

### 1. Product Export Workflow

```
[START]
   │
   ▼
[User Initiates Export]
   │
   ▼
[Create/Update Product Layer]
   │
   ▼
[Build Export Payload]
   │ • Map product data
   │ • Handle variants
   │ • Encode images (base64)
   │
   ▼
[Compute Checksum]
   │
   ▼
[Compare with Previous]
   │
   ├───[Changed?]───NO───▶[Skip Export]
   │
   YES
   │
   ▼
[Check Existing Mapping]
   │
   ├───[Exists?]───YES───▶[Update Product]
   │
   NO
   │
   ▼
[Create New Product]
   │
   ▼
[Store Mapping]
   │
   ▼
[Update Checksum]
   │
   ▼
[END]
```

### 2. Order Import Workflow

```
[START]
   │
   ▼
[Shopify Webhook Triggered]
   │
   ▼
[Verify HMAC Signature]
   │
   ├───[Valid?]───NO───▶[Reject & Log]
   │
   YES
   │
   ▼
[Check Duplicate]
   │
   ├───[Duplicate?]───YES───▶[Skip]
   │
   NO
   │
   ▼
[Create Queue Entry]
   │
   ▼
[Cron Job Picks Up]
   │
   ▼
[Map Order Data]
   │ • Customer lookup/create
   │ • Product lookup by SKU
   │ • Address mapping
   │
   ▼
[Create Sale Order]
   │
   ▼
[Apply Auto-Workflow]
   │ • Confirm order
   │ • Create delivery
   │ • Create invoice
   │
   ▼
[Store Mapping]
   │
   ▼
[END]
```

### 3. Inventory Sync Workflow

```
[START]
   │
   ▼
[Stock Change in Odoo]
   │ • Incoming shipment
   │ • Outgoing delivery
   │ • Inventory adjustment
   │
   ▼
[Check Location Mapping]
   │
   ├───[Mapped?]───NO───▶[Ignore]
   │
   YES
   │
   ▼
[Compute Available Qty]
   │
   ▼
[Add to Stock Queue]
   │
   ▼
[Cron Processes Queue]
   │
   ▼
[Send to Shopify]
   │
   ▼
[Update Shopify Inventory]
   │
   ▼
[Mark Queue Done]
   │
   ▼
[END]
```

---

## API Integration

### Shopify REST API

**Base URL**: `https://{shop}.myshopify.com/admin/api/2025-01`

**Authentication**: Access Token (Admin API)

**Key Endpoints**:
```
POST   /products.json              # Create product
PUT    /products/{id}.json         # Update product
GET    /orders.json                # List orders
GET    /orders/{id}.json           # Get order
POST   /orders/{id}/fulfillments   # Create fulfillment
POST   /inventory_levels/set.json  # Update inventory
GET    /customers.json               # List customers
```

**Rate Limiting**: Shopify GraphQL Admin API rate limits apply

**Retry Policy**:
- 5 retries for transient errors (5xx, timeouts)
- Exponential backoff: 1s, 2s, 4s, 8s, 16s
- Dead letter queue after max retries

---

## Queue System

### Queue Types

| Queue | Purpose | Processing Frequency |
|-------|---------|---------------------|
| Product Export Queue | Product sync jobs | Every 5 minutes |
| Order Import Queue | New orders from webhooks | Every 1 minute |
| Stock Update Queue | Inventory sync | Every 10 minutes |
| Price Update Queue | Price changes | Every 15 minutes |
| Image Update Queue | Product images | Every 30 minutes |
| Customer Import Queue | Customer sync | Every 30 minutes |

### Queue Status Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   PENDING   │────▶│  PROCESSING │────▶│    DONE     │     │   FAILED    │
│             │     │             │     │             │     │             │
│ New queue   │     │ Worker      │     │ Success     │     │ Max retries │
│ entries     │     │ picked up   │     │             │     │ exceeded    │
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                   │
                                            ┌──────────────────────┘
                                            ▼
                                     ┌─────────────┐
                                     │   RETRY     │
                                     │   (Manual   │
                                     │   or Auto)  │
                                     └─────────────┘
```

---

## Security Model

### Authentication
- **API Access**: OAuth 2.0 / Admin API Access Token
- **Webhook Verification**: HMAC-SHA256 signature validation
- **HTTPS Enforcement**: All API calls use TLS 1.2+

### Data Protection
- No credential storage in logs
- Request/response payload filtering for sensitive data
- Odoo access control lists (ACLs) for model security

### Webhook Security
```python
# HMAC Verification
computed_hmac = hmac.new(
    webhook_secret.encode(),
    request_body,
    hashlib.sha256
).hexdigest()

if not hmac.compare_digest(computed_hmac, received_hmac):
    raise SecurityError("Invalid webhook signature")
```

---

## Configuration Guide

### 1. Shopify Store Setup

1. Navigate to: `Shopify → Configuration → Stores`
2. Click **Create**
3. Configure:
   - **Shop URL**: `your-store.myshopify.com`
   - **Access Token**: Admin API token from Shopify
   - **Webhook Secret**: For webhook verification
   - **Company**: Default company for orders
   - **Currency**: Default currency

### 2. Location Mapping

1. Go to: `Shopify → Configuration → Locations`
2. Map Odoo warehouses/locations to Shopify locations
3. This enables accurate inventory sync

### 3. Webhook Configuration (in Shopify Admin)

**Required Webhooks**:
```
orders/create    → https://your-odoo.com/shopify/webhook/order
orders/updated   → https://your-odoo.com/shopify/webhook/order
orders/cancelled → https://your-odoo.com/shopify/webhook/cancel
refunds/create   → https://your-odoo.com/shopify/webhook/refund
```

### 4. Cron Job Configuration

All cron jobs are auto-created on installation. Typical intervals:
- Order processing: Every 1 minute
- Product export: Every 5 minutes
- Stock sync: Every 10 minutes

---

## Technical Specifications

### Dependencies
- **Odoo**: Version 19.0
- **Python**: 3.10+
- **External Libraries**:
  - `requests` (HTTP client)
  - `openpyxl` (Excel import)

### Database Schema

**Core Tables**:
```
shopify_store              # Store configuration
shopify_product_layer      # Export staging
shopify_product_map        # Product ID mapping
shopify_variant_map        # Variant ID mapping
shopify_order_queue        # Order queue
shopify_order_map          # Order ID mapping
shopify_import_queue       # Import queue
shopify_stock_queue        # Stock sync queue
shopify_customer_import    # Customer import queue
shopify_sync_log           # Activity logging
```

### Performance Metrics

- **Order Import**: ~2-5 seconds per order
- **Product Export**: ~3-8 seconds per product
- **Stock Sync**: ~1 second per location/product
- **Webhook Processing**: <500ms (queue creation only)

---

## Deployment & Operations

### Installation Steps

```bash
# 1. Copy module to Odoo addons path
cp -r custom_odoo_shopify_connector /opt/odoo/addons/

# 2. Install Python dependencies
pip install openpyxl

# 3. Update Odoo app list
# (Via UI: Apps → Update Apps List)

# 4. Install module
# (Via UI: Apps → Search "Shopify Connector" → Install)

# 5. Configure first store
# (Via UI: Shopify → Configuration → Stores → Create)
```

### Monitoring

**Key Logs to Monitor**:
```bash
# Real-time order processing
sudo journalctl -u odoo -f | grep "order_import"

# Product export status
sudo journalctl -u odoo -f | grep "product_export"

# Failed jobs
sudo journalctl -u odoo -f | grep "status.*failed"

# Webhook activity
sudo journalctl -u odoo -f | grep "webhook"
```

### Troubleshooting

| Issue | Solution |
|-------|----------|
| "missing_product_id" error | Check HTTPS enforcement, verify API token |
| Webhooks not received | Verify webhook URL, check HMAC secret |
| Orders stuck in queue | Check cron jobs are running, review error logs |
| Inventory not syncing | Verify location mapping, check stock queue |
| Products skipped | Use "Force Export" option if deleted on Shopify |

---

## Feature Summary

### Product Sync
- ✅ Simple products export
- ✅ Variant products with attributes
- ✅ Product images (base64 encoding)
- ✅ SEO metadata (title, description, tags)
- ✅ Inventory quantity sync
- ✅ Price synchronization
- ✅ Category mapping

### Order Sync
- ✅ Real-time webhook import
- ✅ Customer auto-creation/matching
- ✅ Order line mapping by SKU
- ✅ Tax calculation
- ✅ Shipping method mapping
- ✅ Auto-confirmation workflows
- ✅ Multi-currency support

### Inventory Sync
- ✅ Real-time stock updates
- ✅ Multi-location support
- ✅ Available quantity calculation
- ✅ Reserved quantity handling

### Fulfillment
- ✅ Shipping notification sync
- ✅ Tracking number export
- ✅ Fulfillment status update

### Refunds
- ✅ Credit note to refund sync
- ✅ Line-level refund support
- ✅ Reason code mapping

---

## License & Support

- **License**: LGPL-3
- **Author**: Gultaj Khan (@misri12)
- **Contact**: gultajkhan980@gmail.com
- **GitHub**: https://github.com/misri12

---

**Document Version**: 1.0
**Last Updated**: April 2026
**Module Version**: 1.0.0
