# iZone UAT Results + Screenshot Proof — 2026-07-28

**Environment**
- Odoo: https://test.drpaws.ai · DB `pet_spot_elsahel_test` · module **`custom_odoo_shopify_connector` 19.0.1.9.0**
- Shopify: Pet Spot `ucbah1-5e.myshopify.com` (linked store on Test)
- Proof folder: `/home/sabry/iZone_UAT_Proof_20260728/`

**Overall:** **PASS** (7/7 executed Odoo scenarios)  
**Automated tests:** `shopify_wp_ab` + `shopify_wp_p1p2` + `shopify_wp_i` → **37/37 pass**

---

## 1. Development completed (all phases)

| Phase | Items | Status |
|-------|-------|--------|
| **P0** | WP-A payment on update · WP-B stock gate | Done (19.0.1.7.0) |
| **P1** | WP-C return after cancel · WP-D address · WP-E inbound tracking · WP-F damaged no-restock | Done (19.0.1.9.0) |
| **P2** | WP-G notes/currency · WP-H tags · WP-J shipping/payment method update | Done (19.0.1.9.0) |
| **WP-I** | Reopen / archive (metadata / manual review) | **Done** (19.0.1.9.0) |

---

## 2. Scenario execution results (test.drpaws.ai)

| ID | Title | Result | Evidence |
|----|-------|--------|----------|
| SO-01 | Happy path paid + note/tags | **PASS** | SO `8928163146` · payment `PBNK1/2026/00003` · tags `uat, izone, phase-all` · note synced |
| SO-03 | Pending→Paid payment sync | **PASS** | SO `8928163147` · payment `PBNK1/2026/00004` |
| BC-02 | Stock gate blocks confirm | **PASS** | SO `8928163148` stayed **draft** with zero stock |
| SO-17/20 | Fulfillment + inbound tracking | **PASS** | Picking `WH/OUT/00031` **Done** · tracking `TRK-UAT-8928163146` |
| SO-05 | Cancel before shipment | **PASS** | SO `8928163149` **cancel** |
| SO-14 | Shipping address update | **PASS** | Street `New Marina Road 22` · city `Alexandria` |
| BC-09 | Paymob payment simulated in Odoo | **PASS** | Same payment as SO-01 (Odoo journal, no live Paymob) |

Raw JSON: `uat_results_20260728163146.json`

---

## 3. PetSpot Shopify proof

Created live Admin API order on **ucbah1-5e**:

| Field | Value |
|-------|-------|
| Order | **#1003** |
| Shopify id | `7291442921753` |
| Amount | 150.00 EGP |
| Status | pending |
| Tags | `cursor-proof, izone, uat` |
| Note | UAT proof order from Cursor … |
| Admin URL | https://admin.shopify.com/store/ucbah1-5e/orders/7291442921753 |

JSON: `shopify_order_1003_proof.json`  
Store product count via API: **2032**

> **Note:** Headless browser screenshots of Shopify Admin were blocked by **Cloudflare** (“Verify you are human”). Shopify proof is therefore **Admin API JSON** + order #1003. Odoo UI screenshots below are full visual proof on test.drpaws.ai.

---

## 4. Screenshot proof (Odoo)

Directory: `/home/sabry/iZone_UAT_Proof_20260728/screenshots/`

| File | What it shows |
|------|----------------|
| `00_proof_summary.png` | HTML summary board of all PASS results |
| `01_odoo_login_page.png` | test.drpaws.ai login |
| `02_odoo_home.png` | Odoo home after login |
| `03_odoo_SO01_happy_path.png` | SO 8928163146 Sales Order · delivered/invoiced/paid · Shopify note |
| `04_odoo_BC02_stock_gate_draft.png` | SO 8928163148 stuck in **Quotation** (stock gate) |
| `05_odoo_picking_tracking.png` | WH/OUT/00031 Done · source 8928163146 |
| `06_odoo_SO05_cancelled.png` | Cancelled SO |
| `07_odoo_SO14_address_update.png` | Address-updated SO |
| `11_shopify_uat_order_detail.png` | Cloudflare challenge (Shopify blocked) |

Open the summary: `PROOF_SUMMARY.html`

---

## 5. How to re-open proof in UI

1. https://test.drpaws.ai/web/login?db=pet_spot_elsahel_test (admin)  
2. Sales → Orders → search `8928163146` / `8928163147` / `8928163148` / `8928163149`  
3. Inventory → Transfers → `WH/OUT/00031`  
4. Shopify Admin → Orders → `#1003` (manual browser; may need Cloudflare)

---

## 6. Next (optional)

- Turn on **Require Stock Before Confirm** permanently for iZone store when deploying to A-Zone  
- Sync connector 19.0.1.9.0 to iZone A-Zone after PetSpot UAT sign-off  
- WP-I done on PetSpot Test — await approval before iZone deploy  
- Full live Shopify→webhook→Odoo E2E once Cloudflare/session allows browser automation (or use storefront checkout manually)


## WP-I follow-up

See `wp_i/WP_I_REPORT.md` and `/home/sabry/iZone_UAT_Proof_20260728/wp_i/`.
