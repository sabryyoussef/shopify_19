import requests
import time
import logging

from odoo import _
from odoo.exceptions import UserError

from .retry_policy import TRANSIENT_HTTP_STATUS_CODES, backoff_seconds

_logger = logging.getLogger(__name__)


# class ShopifyAPI:
#     def __init__(self, shop_url, access_token, api_key=None, api_secret=None):
#         self.shop_url = (shop_url or "").strip().rstrip("/")
#         # Ensure HTTPS - Shopify Admin API requires HTTPS
#         # Strip any existing protocol and force HTTPS
#         if self.shop_url.lower().startswith("http://"):
#             self.shop_url = "https://" + self.shop_url[7:]
#         elif self.shop_url.lower().startswith("https://"):
#             self.shop_url = "https://" + self.shop_url[8:]
#         else:
#             self.shop_url = "https://" + self.shop_url
#         self.access_token = access_token
#         self.api_key = api_key
#         self.api_secret = api_secret
#         self.base_url = "%s/admin/api/2025-01" % self.shop_url
#         self.headers = {
#             "Content-Type": "application/json",
#             "X-Shopify-Access-Token": self.access_token,
#         }
#         self.last_response_headers = {}

#     def _request(self, method, path, params=None, data=None, max_retries=5):
#         """Low-level JSON request helper with transient retry backoff."""
#         url = "%s%s" % (self.base_url, path)

#         # Debug logging for request diagnostics
#         _logger.info(
#             "Shopify API request | method=%s | url=%s | data_preview=%s",
#             method,
#             url,
#             str(data)[:150] if data else "none",
#         )

#         attempt = 0
#         while True:
#             attempt += 1
#             try:
#                 response = requests.request(
#                     method,
#                     url,
#                     headers=self.headers,
#                     params=params,
#                     json=data,
#                     timeout=30,
#                 )
#             except requests.RequestException as e:
#                 if attempt <= max_retries:
#                     time.sleep(backoff_seconds(attempt, base_seconds=2, max_seconds=60, jitter_seconds=2))
#                     continue
#                 raise UserError(_("Shopify request error: %s") % e)

#             if (
#                 response.status_code in TRANSIENT_HTTP_STATUS_CODES
#                 and attempt <= max_retries
#             ):
#                 retry_after = response.headers.get("Retry-After")
#                 try:
#                     sleep_seconds = int(retry_after) if retry_after else None
#                 except Exception:
#                     sleep_seconds = None
#                 if sleep_seconds is None:
#                     sleep_seconds = backoff_seconds(
#                         attempt,
#                         base_seconds=2,
#                         max_seconds=60,
#                         jitter_seconds=2,
#                     )
#                 time.sleep(sleep_seconds)
#                 continue

#             if not response.ok:
#                 err_msg = response.text
#                 try:
#                     body = response.json()
#                     if isinstance(body, dict):
#                         errors = body.get("errors") or body.get("error")
#                         if errors:
#                             err_msg = (
#                                 errors
#                                 if isinstance(errors, str)
#                                 else ", ".join(
#                                     str(e)
#                                     for e in (
#                                         errors
#                                         if isinstance(errors, list)
#                                         else [errors]
#                                     )
#                                 )
#                             )
#                 except Exception:
#                     pass
#                 raise UserError(
#                     _("Shopify API error %s: %s")
#                     % (response.status_code, err_msg)
#                 )

#             self.last_response_headers = dict(response.headers or {})

#             # Debug logging for API diagnostics
#             _logger.info(
#                 "Shopify API response | method=%s | path=%s | status=%s | content-type=%s | body_preview=%s",
#                 method,
#                 path,
#                 response.status_code,
#                 response.headers.get("Content-Type", "unknown"),
#                 response.text[:200] if response.text else "empty",
#             )

#             if response.text:
#                 return response.json()
#             return {}

#     def _paginate(self, path, root_key, params=None, max_retries=5):
#         """Cursor-based pagination with shared transient retry backoff."""
#         all_items = []
#         url = "%s%s" % (self.base_url, path)
#         query_params = params or {}

#         while url:
#             attempt = 0
#             while True:
#                 attempt += 1
#                 try:
#                     response = requests.get(
#                         url,
#                         headers=self.headers,
#                         params=query_params,
#                         timeout=30,
#                     )
#                 except requests.RequestException as e:
#                     if attempt <= max_retries:
#                         time.sleep(backoff_seconds(attempt, base_seconds=2, max_seconds=60, jitter_seconds=2))
#                         continue
#                     raise UserError(_("Shopify request error: %s") % e)

#                 if (
#                     response.status_code in TRANSIENT_HTTP_STATUS_CODES
#                     and attempt <= max_retries
#                 ):
#                     retry_after = response.headers.get("Retry-After")
#                     try:
#                         sleep_seconds = int(retry_after) if retry_after else None
#                     except Exception:
#                         sleep_seconds = None
#                     if sleep_seconds is None:
#                         sleep_seconds = backoff_seconds(
#                             attempt,
#                             base_seconds=2,
#                             max_seconds=60,
#                             jitter_seconds=2,
#                         )
#                     time.sleep(sleep_seconds)
#                     continue

#                 if not response.ok:
#                     raise UserError(
#                         _("Shopify API error %s: %s")
#                         % (response.status_code, response.text)
#                     )

#                 data = response.json() if response.text else {}
#                 items = data.get(root_key, [])
#                 all_items.extend(items)

#                 # After first page, subsequent requests should rely on the next link only
#                 query_params = {}
#                 url = self._extract_next_link(response.headers)
#                 break

#         return all_items

#     @staticmethod
#     def _extract_next_link(headers):
#         """Extract next page URL from Shopify Link header."""
#         link_header = headers.get("Link") or headers.get("link")
#         if not link_header:
#             return None

#         # Example: <https://shop.myshopify.com/admin/api/2024-01/products.json?page_info=xxx&limit=250>; rel="next"
#         parts = [p.strip() for p in link_header.split(",")]
#         for part in parts:
#             if 'rel="next"' in part:
#                 url_part = part.split(";")[0].strip()
#                 if url_part.startswith("<") and url_part.endswith(">"):
#                     return url_part[1:-1]
#         return None

#     def ping(self):
#         self.get_products(limit=1)
#         return True

#     def get_products(self, **params):
#         params.setdefault("limit", 250)
#         return self._paginate("/products.json", "products", params=params)

#     def get_orders(self, **params):
#         params.setdefault("limit", 250)
#         return self._paginate("/orders.json", "orders", params=params)

#     def get_customers(self, **params):
#         params.setdefault("limit", 250)
#         return self._paginate("/customers.json", "customers", params=params)

#     def get_inventory(self, **params):
#         res = self._request("GET", "/inventory_levels.json", params=params)
#         return res.get("inventory_levels", [])

#     def update_inventory_level(self, inventory_item_id, available, location_id):
#         data = {
#             "location_id": location_id,
#             "inventory_item_id": inventory_item_id,
#             "available": int(available),
#         }
#         return self._request("POST", "/inventory_levels/set.json", data=data)

#     def get_locations(self, **params):
#         """Fetch all locations from Shopify."""
#         params.setdefault("limit", 250)
#         return self._paginate("/locations.json", "locations", params=params)

#     # Product import helpers used by product import workflow
#     def get_products_by_date(self, **params):
#         """Fetch products filtered by date fields (created_at_min / updated_at_min).

#         Pagination is handled transparently via _paginate.
#         """
#         params.setdefault("limit", 250)
#         return self._paginate("/products.json", "products", params=params)

#     def get_product_by_id(self, product_id):
#         """Fetch a single product by Shopify ID."""
#         path = "/products/%s.json" % product_id
#         return self._request("GET", path)

#     def get_customer_by_id(self, customer_id):
#         """Fetch a single customer by Shopify ID."""
#         path = "/customers/%s.json" % customer_id
#         return self._request("GET", path)

#     def create_product(self, product_payload):
#         """Create a product in Shopify. product_payload follows REST Product resource.
#         Returns API response with created product (id, variants, etc.).
#         """
#         variants = (product_payload or {}).get("variants") or []
#         images = (product_payload or {}).get("images") or []
        
#         _logger.info(
#             "Shopify API create_product | title=%s | variants=%s | images=%s",
#             (product_payload or {}).get("title"),
#             len(variants),
#             len(images),
#         )
        
#         # Log first variant inventory if available
#         if variants and len(variants) > 0:
#             first_variant = variants[0]
#             _logger.info(
#                 "First variant data | sku=%s | price=%s | inventory_qty=%s",
#                 first_variant.get("sku"),
#                 first_variant.get("price"),
#                 first_variant.get("inventory_quantity"),
#             )
        
#         return self._request("POST", "/products.json", data={"product": product_payload})

#     def update_product(self, shopify_product_id, product_payload):
#         """Update an existing product in Shopify."""
#         path = "/products/%s.json" % shopify_product_id
#         payload = dict(product_payload or {})
#         # Shopify product update requires product.id in the body.
#         payload["id"] = int(shopify_product_id)
        
#         variants = payload.get("variants") or []
#         images = payload.get("images") or []
        
#         _logger.info(
#             "Shopify API update_product | id=%s | title=%s | variants=%s | images=%s",
#             shopify_product_id,
#             payload.get("title"),
#             len(variants),
#             len(images),
#         )
        
#         # Log first variant inventory if available
#         if variants and len(variants) > 0:
#             first_variant = variants[0]
#             _logger.info(
#                 "First variant data | sku=%s | price=%s | inventory_qty=%s",
#                 first_variant.get("sku"),
#                 first_variant.get("price"),
#                 first_variant.get("inventory_quantity"),
#             )
        
#         return self._request("PUT", path, data={"product": payload})

#     def update_variant(self, variant_id, variant_payload):
#         """Update an existing product variant in Shopify.

#         Expected payload format (REST):
#             {"variant": {"id": VARIANT_ID, "price": "123.45", ...}}
#         """
#         path = "/variants/%s.json" % variant_id
#         return self._request("PUT", path, data=variant_payload)

#     # Product image helpers
#     def get_product_images(self, product_id, **params):
#         """Fetch all images for a given Shopify product."""
#         path = "/products/%s/images.json" % product_id
#         res = self._request("GET", path, params=params)
#         return res.get("images", [])

#     def delete_product_image(self, product_id, image_id):
#         """Delete a specific image from a Shopify product."""
#         path = "/products/%s/images/%s.json" % (product_id, image_id)
#         # Shopify returns 200 with empty body on successful delete
#         return self._request("DELETE", path)

#     def create_product_image(self, product_id, image_payload):
#         """Create a new image on a Shopify product using base64 attachment.

#         Expected payload format:
#             {
#                 "image": {
#                     "attachment": "<BASE64_IMAGE_DATA>"
#                 }
#             }
#         """
#         path = "/products/%s/images.json" % product_id
#         return self._request("POST", path, data=image_payload)

class ShopifyAPI:
    def __init__(self, shop_url, access_token):
        # ✅ Clean URL normalization
        shop_url = (shop_url or "").strip()
        shop_url = shop_url.replace("http://", "").replace("https://", "")
        self.shop_url = f"https://{shop_url.strip('/')}"

        self.access_token = access_token
        self.base_url = f"{self.shop_url}/admin/api/2025-01"

        self.headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": self.access_token,
        }

        self.last_response_headers = {}

    # =========================================================
    # CORE REQUEST HANDLER
    # =========================================================
    def _request(self, method, path, params=None, data=None, max_retries=5):
        url = f"{self.base_url}{path}"

        _logger.info(
            "Shopify API request | %s %s | payload=%s",
            method,
            url,
            str(data)[:200] if data else "none",
        )

        attempt = 0

        while True:
            attempt += 1
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self.headers,
                    params=params,
                    json=data,
                    timeout=30,
                )
            except requests.RequestException as e:
                if attempt <= max_retries:
                    time.sleep(backoff_seconds(attempt))
                    continue
                raise UserError(_("Shopify request error: %s") % e)

            # ✅ Rate limit handling
            if response.status_code == 429 and attempt <= max_retries:
                retry_raw = response.headers.get("Retry-After", 2)
                try:
                    retry_after = int(float(retry_raw))
                except Exception:
                    retry_after = 2
                time.sleep(retry_after)
                continue

            # ✅ Retry transient errors
            if response.status_code in TRANSIENT_HTTP_STATUS_CODES and attempt <= max_retries:
                time.sleep(backoff_seconds(attempt))
                continue

            # ❌ Hard failure
            if not response.ok:
                raise UserError(
                    _("Shopify API error %s: %s")
                    % (response.status_code, response.text)
                )

            self.last_response_headers = dict(response.headers or {})

            _logger.info(
                "Shopify API response | status=%s | body=%s",
                response.status_code,
                response.text[:300],
            )

            return response.json() if response.text else {}

    # =========================================================
    # PRODUCTS
    # =========================================================
    def get_products(self, **params):
        """Fetch products from Shopify."""
        params.setdefault("limit", 250)
        res = self._request("GET", "/products.json", params=params)
        return res.get("products", [])

    def get_product_by_id(self, product_id):
        """Fetch a single product by Shopify ID."""
        path = f"/products/{product_id}.json"
        res = self._request("GET", path)
        return res.get("product")

    def get_orders(self, **params):
        """Fetch orders from Shopify."""
        params.setdefault("limit", 250)
        res = self._request("GET", "/orders.json", params=params)
        return res.get("orders", [])

    def get_customers(self, **params):
        """Fetch customers from Shopify."""
        params.setdefault("limit", 250)
        res = self._request("GET", "/customers.json", params=params)
        return res.get("customers", [])

    def get_customer_by_id(self, customer_id):
        """Fetch a single customer by Shopify ID."""
        path = f"/customers/{customer_id}.json"
        return self._request("GET", path)

    # =========================================================
    # WEBHOOKS (registration / reconciliation)
    # =========================================================
    def get_webhooks(self, **params):
        """List webhook subscriptions registered on the store."""
        params.setdefault("limit", 250)
        res = self._request("GET", "/webhooks.json", params=params)
        return res.get("webhooks", [])

    def create_webhook(self, topic, address, fmt="json"):
        """Register a webhook subscription for a topic -> callback address."""
        res = self._request(
            "POST",
            "/webhooks.json",
            data={"webhook": {"topic": topic, "address": address, "format": fmt}},
        )
        return res.get("webhook")

    def update_webhook(self, webhook_id, address, fmt="json"):
        """Update an existing webhook subscription's callback address."""
        res = self._request(
            "PUT",
            f"/webhooks/{webhook_id}.json",
            data={"webhook": {"id": int(webhook_id), "address": address, "format": fmt}},
        )
        return res.get("webhook")

    def delete_webhook(self, webhook_id):
        """Delete a webhook subscription."""
        return self._request("DELETE", f"/webhooks/{webhook_id}.json")

    def create_product(self, product_payload, location_id=None):
        res = self._request(
            "POST",
            "/products.json",
            data={"product": product_payload},
        )

        product = res.get("product")
        if not product or not product.get("id"):
            raise UserError("❌ Shopify did not return product ID")

        _logger.info("✅ Product created | id=%s", product["id"])

        # ✅ Inventory fix
        if location_id:
            self._sync_inventory(
                product,
                location_id,
                source_variants=(product_payload or {}).get("variants") or [],
            )

        return product

    def update_product(self, product_id, product_payload, location_id=None):
        payload = dict(product_payload or {})
        payload["id"] = int(product_id)

        res = self._request(
            "PUT",
            f"/products/{product_id}.json",
            data={"product": payload},
        )

        product = res.get("product")
        if not product:
            raise UserError("❌ Shopify update failed (no product in response)")

        _logger.info("✅ Product updated | id=%s", product_id)

        # ✅ Inventory fix
        if location_id:
            self._sync_inventory(
                product,
                location_id,
                source_variants=(product_payload or {}).get("variants") or [],
            )

        return product

    # =========================================================
    # INVENTORY (CRITICAL FIX)
    # =========================================================
    def _sync_inventory(self, product, location_id, source_variants=None):
        variants = product.get("variants", [])
        source_variants = source_variants or []
        source_qty_by_id = {}
        source_qty_by_sku = {}
        for source_variant in source_variants:
            if not isinstance(source_variant, dict):
                continue
            qty = source_variant.get("inventory_quantity")
            variant_id = source_variant.get("id")
            sku = (source_variant.get("sku") or "").strip()
            if variant_id not in (None, "", False):
                source_qty_by_id[str(variant_id)] = qty
            if sku:
                source_qty_by_sku[sku] = qty

        for variant in variants:
            inventory_item_id = variant.get("inventory_item_id")
            variant_id = variant.get("id")
            sku = (variant.get("sku") or "").strip()

            if inventory_item_id is None:
                continue

            self.set_inventory_item_tracked(inventory_item_id, tracked=True)

            qty = source_qty_by_id.get(str(variant_id))
            if qty in (None, "", False):
                qty = source_qty_by_sku.get(sku)
            if qty in (None, "", False):
                qty = variant.get("inventory_quantity")
            if qty in (None, "", False):
                qty = 0

            self.update_inventory_level(
                inventory_item_id=inventory_item_id,
                location_id=location_id,
                available=qty,
            )

            _logger.info(
                "📦 Inventory synced | item=%s | qty=%s",
                inventory_item_id,
                qty,
            )

    def set_inventory_item_tracked(self, inventory_item_id, tracked=True):
        return self._request(
            "PUT",
            f"/inventory_items/{inventory_item_id}.json",
            data={
                "inventory_item": {
                    "id": int(inventory_item_id),
                    "tracked": bool(tracked),
                }
            },
        )

    def update_inventory_level(self, inventory_item_id, location_id, available):
        return self._request(
            "POST",
            "/inventory_levels/set.json",
            data={
                "location_id": location_id,
                "inventory_item_id": inventory_item_id,
                "available": int(available),
            },
        )

    def get_inventory(self, **params):
        """Fetch inventory levels from Shopify."""
        res = self._request("GET", "/inventory_levels.json", params=params)
        return res.get("inventory_levels", [])

    # =========================================================
    # IMAGES (FIXED - URL BASED)
    # =========================================================
    def upload_image_by_url(self, product_id, image_url):
        if not image_url:
            return

        payload = {
            "image": {
                "src": image_url
            }
        }

        return self._request(
            "POST",
            f"/products/{product_id}/images.json",
            data=payload,
        )

    def get_product_images(self, product_id, **params):
        """Fetch images for a Shopify product."""
        res = self._request(
            "GET",
            f"/products/{product_id}/images.json",
            params=params,
        )
        return res.get("images", [])

    def get_product_metafields(self, product_id, **params):
        """Fetch product metafields from Shopify."""
        params.setdefault("limit", 250)
        res = self._request(
            "GET",
            f"/products/{product_id}/metafields.json",
            params=params,
        )
        return res.get("metafields", [])

    def upsert_product_metafield(self, product_id, namespace, key, mtype, value):
        """Create or update a product metafield identified by namespace+key."""
        existing = self.get_product_metafields(product_id)
        target = next(
            (
                mf
                for mf in existing
                if (mf.get("namespace") == namespace and mf.get("key") == key)
            ),
            None,
        )
        payload = {
            "metafield": {
                "namespace": namespace,
                "key": key,
                "type": mtype,
                "value": value,
            }
        }
        if target and target.get("id"):
            return self._request(
                "PUT",
                f"/metafields/{target.get('id')}.json",
                data=payload,
            )
        return self._request(
            "POST",
            f"/products/{product_id}/metafields.json",
            data=payload,
        )

    def upsert_product_metafields(self, product_id, metafields):
        """Bulk upsert product metafields."""
        results = []
        for metafield in metafields or []:
            namespace = metafield.get("namespace")
            key = metafield.get("key")
            mtype = metafield.get("type")
            value = metafield.get("value")
            if not (namespace and key and mtype) or value in (None, False, ""):
                continue
            results.append(
                self.upsert_product_metafield(
                    product_id=product_id,
                    namespace=namespace,
                    key=key,
                    mtype=mtype,
                    value=value,
                )
            )
        return results

    # =========================================================
    # VARIANTS (BEST PRACTICE)
    # =========================================================
    def update_variant(self, variant_id, price=None, sku=None):
        payload = {
            "variant": {
                "id": variant_id,
            }
        }

        if price is not None:
            payload["variant"]["price"] = str(price)

        if sku:
            payload["variant"]["sku"] = sku

        return self._request(
            "PUT",
            f"/variants/{variant_id}.json",
            data=payload,
        )

    # =========================================================
    # HELPERS
    # =========================================================
    def ping(self):
        """Test connection to Shopify by fetching a single product."""
        try:
            self._request("GET", "/products.json", params={"limit": 1})
            return True
        except Exception as e:
            _logger.error("Shopify ping failed: %s", str(e))
            raise

    def get_locations(self):
        res = self._request("GET", "/locations.json")
        return res.get("locations", [])

    def get_primary_location(self):
        locations = self.get_locations()
        return locations[0]["id"] if locations else None