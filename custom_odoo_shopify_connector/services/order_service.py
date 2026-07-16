from odoo import _
from odoo.exceptions import ValidationError
from odoo.tools import float_round


class OrderService:
    def __init__(self, env, import_service=None):
        self.env = env
        # import_service is optional and used for tax / additional helpers
        self.import_service = import_service

    def _find_existing_order(self, payload, store):
        SaleOrder = self.env["sale.order"]
        shopify_order_id = payload.get("id")
        client_ref = "SHOPIFY-%s" % shopify_order_id if shopify_order_id else False

        if shopify_order_id:
            existing = SaleOrder.search([("shopify_order_id", "=", str(shopify_order_id))], limit=1)
            if existing:
                return existing

            mapping = (
                self.env["shopify.order.map"]
                .search(
                    [
                        ("store_id", "=", store.id if store else False),
                        ("shopify_order_id", "=", str(shopify_order_id)),
                    ],
                    limit=1,
                )
            )
            if mapping and mapping.odoo_order_id:
                return mapping.odoo_order_id

        if client_ref:
            existing = SaleOrder.search([("client_order_ref", "=", client_ref)], limit=1)
            if existing:
                return existing

        return False

    def _build_order_vals(self, payload, store, partner):
        shopify_order_id = payload.get("id")
        client_ref = "SHOPIFY-%s" % shopify_order_id if shopify_order_id else False

        order_vals = {
            "partner_id": partner.id,
            "company_id": store.company_id.id if store and store.company_id else False,
            "client_order_ref": client_ref,
            "origin": payload.get("name"),
            "shopify_order_id": str(shopify_order_id) if shopify_order_id else False,
            "shopify_instance_id": store.id if store else False,
        }
        gateway_names = payload.get("payment_gateway_names") or []
        if isinstance(gateway_names, str):
            gateway_names = [gateway_names]
        if gateway_names:
            order_vals["shopify_payment_gateway"] = gateway_names[0]
        elif payload.get("gateway"):
            order_vals["shopify_payment_gateway"] = payload.get("gateway")
        if payload.get("total_price") is not None:
            try:
                order_vals["shopify_order_total"] = float(payload.get("total_price") or 0.0)
            except (TypeError, ValueError):
                pass
        fulfillment_status = payload.get("fulfillment_status") or "unfulfilled"
        order_vals["shopify_fulfillment_status"] = fulfillment_status
        shopify_user_id = self._resolve_shopify_user_id(store)
        if shopify_user_id:
            order_vals["user_id"] = shopify_user_id

        discount_source = self.extract_discount_sources(payload)
        if discount_source:
            order_vals["shopify_discount_source"] = discount_source

        # Order naming: either use Odoo sequence or Shopify order number with optional prefix
        if store and not store.use_odoo_sequence:
            shopify_name = payload.get("name")  # e.g. "#1044"
            number = (shopify_name or "").lstrip("#") or str(shopify_order_id or "")
            if number:
                prefix = store.order_prefix or ""
                order_vals["name"] = "%s%s" % (prefix, number)

        return order_vals

    def _resolve_shopify_user_id(self, store=None):
        """Salesperson for Shopify-imported orders (cron-safe when store field is set)."""
        if store:
            user = store._resolve_import_order_salesperson_user()
            return user.id if user else False

        Users = self.env["res.users"].sudo()
        shopify_user = self.env.ref(
            "custom_odoo_shopify_connector.user_shopify",
            raise_if_not_found=False,
        )
        if not shopify_user:
            shopify_user = Users.search(
                [("login", "=", "shopify@system.local"), ("active", "=", True)],
                limit=1,
            )
        if not shopify_user:
            shopify_user = Users.search(
                [("name", "=", "Shopify"), ("active", "=", True)],
                order="id asc",
                limit=1,
            )
        if not shopify_user:
            shopify_user = Users.search(
                [("login", "=", "shopify.connector.import"), ("active", "=", True)],
                limit=1,
            )
        return shopify_user.id if shopify_user else False

    def _ensure_order_mapping(self, store, shopify_order_id, order):
        if not (store and shopify_order_id):
            return
        self.env["shopify.order.map"].sudo().create(
            {
                "store_id": store.id,
                "shopify_order_id": str(shopify_order_id),
                "odoo_order_id": order.id,
            }
        )

    def _prepare_line_product_lookups(self, store, line_items):
        ProductProduct = self.env["product.product"]
        VariantMap = self.env["shopify.variant.map"]

        variant_ids = list(
            {
                str(item.get("variant_id"))
                for item in (line_items or [])
                if item.get("variant_id")
            }
        )
        skus = list({(item.get("sku") or "").strip() for item in (line_items or []) if item.get("sku")})

        variant_map_by_id = {}
        if store and variant_ids:
            variant_maps = VariantMap.search(
                [
                    ("store_id", "=", store.id),
                    ("shopify_variant_id", "in", variant_ids),
                ]
            )
            variant_map_by_id = {str(m.shopify_variant_id): m for m in variant_maps}

        product_by_variant_id = {}
        if variant_ids:
            products_by_variant = ProductProduct.search([("shopify_variant_id", "in", variant_ids)])
            product_by_variant_id = {str(p.shopify_variant_id): p for p in products_by_variant}

        product_by_sku = {}
        if skus:
            products_by_sku = ProductProduct.search([("default_code", "in", skus)])
            product_by_sku = {p.default_code: p for p in products_by_sku if p.default_code}

        return variant_map_by_id, product_by_variant_id, product_by_sku

    def _resolve_line_product(
        self,
        item,
        store,
        variant_map_by_id,
        product_by_variant_id,
        product_by_sku,
    ):
        ProductProduct = self.env["product.product"]

        sku = item.get("sku")
        name = item.get("name") or _("Shopify Item")
        price = float(item.get("price") or 0.0)
        variant_id = item.get("variant_id")

        product = False

        # 1) Try explicit variant mapping model
        if variant_id and store:
            mapping = variant_map_by_id.get(str(variant_id))
            if mapping:
                product = mapping.product_id

        # 2) Fallback to direct product search by Shopify variant id
        if not product and variant_id:
            product = product_by_variant_id.get(str(variant_id))

        # 3) Fallback to SKU / internal reference
        if not product and sku:
            product = product_by_sku.get(sku)

        # 4) Auto-create product if still not found (when enabled on store)
        if not product:
            if store and not store.auto_create_product_if_not_found:
                raise ValidationError(
                    _(
                        "Product not found in Odoo for SKU '%(sku)s' (variant %(variant)s). "
                        "Map the variant in Product Mapping or enable Auto Create Products on the store."
                    )
                    % {
                        "sku": sku or "-",
                        "variant": variant_id or "-",
                    }
                )
            vals = {
                "name": name,
                "default_code": sku,
                "lst_price": price,
            }
            product = ProductProduct.create(vals)
            if sku:
                product_by_sku[sku] = product
            if variant_id:
                product_by_variant_id[str(variant_id)] = product

        return product

    def _line_discount_amount(self, item):
        """Return absolute discount amount for a Shopify line item."""
        discount_amount = 0.0
        discount_allocations = item.get("discount_allocations") or []
        if discount_allocations:
            for alloc in discount_allocations:
                try:
                    discount_amount += float(alloc.get("amount") or 0.0)
                except Exception:
                    continue
        else:
            try:
                discount_amount = float(item.get("total_discount") or 0.0)
            except Exception:
                discount_amount = 0.0
        # Order-level proportional share (set by prepare_line_items_with_discounts)
        if item.get("_odoo_order_discount_share") is not None:
            try:
                discount_amount = float(item.get("_odoo_order_discount_share") or 0.0)
            except Exception:
                pass
        return max(discount_amount, 0.0)

    def prepare_line_items_with_discounts(self, payload):
        """
        Return a shallow-copied list of line items with discounts normalized.
        When order total_discounts is present but lines have no allocations /
        total_discount, distribute the order discount proportionally by line total.
        """
        items = [dict(item or {}) for item in (payload.get("line_items") or [])]
        if not items:
            return items

        any_line_discount = any(
            (item.get("discount_allocations") or [])
            or float(item.get("total_discount") or 0.0) > 0.0
            for item in items
        )
        try:
            order_total_discount = float(payload.get("total_discounts") or 0.0)
        except Exception:
            order_total_discount = 0.0

        if any_line_discount or order_total_discount <= 0.0:
            return items

        line_totals = []
        for item in items:
            qty = float(item.get("quantity") or 0.0)
            price = float(item.get("price") or 0.0)
            line_totals.append(max(qty * price, 0.0))
        base = sum(line_totals)
        if not base:
            return items

        allocated = 0.0
        for idx, item in enumerate(items):
            if idx == len(items) - 1:
                share = float_round(order_total_discount - allocated, 2)
            else:
                share = float_round(order_total_discount * (line_totals[idx] / base), 2)
                allocated += share
            item["_odoo_order_discount_share"] = max(share, 0.0)
        return items

    def _compute_discount_pct(self, item, quantity, price):
        discount_amount = self._line_discount_amount(item)
        discount_pct = 0.0
        if quantity and price and discount_amount:
            line_total = quantity * price
            if line_total:
                discount_pct = (discount_amount / line_total) * 100.0
                # Guard: never exceed 100%
                if discount_pct > 100.0:
                    discount_pct = 100.0
        return discount_pct

    def extract_discount_sources(self, payload):
        """
        Classify Shopify discount applications as coupon / automatic / unknown.
        Returns a comma-separated string for storage on sale.order.
        """
        sources = set()
        apps = payload.get("discount_applications") or []
        for app in apps:
            app_type = (app.get("type") or "").lower()
            target = (app.get("target_type") or "").lower()
            code = app.get("code")
            if app_type == "discount_code" or code:
                sources.add("coupon")
            elif app_type in ("automatic", "script") or target:
                sources.add("automatic")
            else:
                sources.add("unknown")
        # Fallbacks when applications are missing but discounts exist
        if not sources:
            has_line_disc = any(
                self._line_discount_amount(item) > 0.0
                for item in (payload.get("line_items") or [])
            )
            try:
                total_discounts = float(payload.get("total_discounts") or 0.0)
            except Exception:
                total_discounts = 0.0
            if has_line_disc or total_discounts > 0.0:
                sources.add("unknown")
        return ",".join(sorted(sources)) if sources else False

    def _build_order_line_vals(self, order, item, store, product):
        quantity = float(item.get("quantity") or 0.0)
        price = float(item.get("price") or 0.0)
        name = item.get("name") or _("Shopify Item")

        discount_pct = self._compute_discount_pct(item, quantity, price)
        discount_amount = self._line_discount_amount(item)

        taxes = self.env["account.tax"]
        if self.import_service and store:
            taxes = self.import_service.get_taxes_for_line(store, item)

        vals = {
            "order_id": order.id,
            "product_id": product.id,
            "name": name,
            "product_uom_qty": quantity,
            "price_unit": float_round(price, 2),
            "discount": discount_pct,
            "shopify_line_item_id": str(item.get("id") or "") or False,
            "shopify_original_price": float_round(price, 2),
            "shopify_line_discount_amount": float_round(discount_amount, 2),
        }
        sale_line_model = self.env["sale.order.line"]
        if "tax_ids" in sale_line_model._fields:
            vals["tax_ids"] = [(6, 0, taxes.ids)]
        elif "tax_id" in sale_line_model._fields:
            vals["tax_id"] = [(6, 0, taxes.ids)]
        return vals

    def _build_missing_variant_map_vals(self, item, store, product, variant_map_by_id):
        VariantMap = self.env["shopify.variant.map"]

        variant_id = item.get("variant_id")
        shopify_product_id = item.get("product_id")
        if (
            not store
            or not product
            or not variant_id
            or not shopify_product_id
            or str(variant_id) in variant_map_by_id
        ):
            return None

        vals_map = {
            "store_id": store.id,
            "product_id": product.id,
            "shopify_product_id": shopify_product_id,
            "shopify_variant_id": str(variant_id),
        }
        # mark as seen to prevent duplicate create vals
        variant_map_by_id[str(variant_id)] = True
        return vals_map

    def _create_shipping_lines(self, order, store, payload):
        if not (store and store.delivery_product_id):
            return
        shipping_lines = payload.get("shipping_lines") or []
        if not shipping_lines:
            return
        # Idempotency: do not duplicate delivery product lines for this order
        existing_ship_lines = order.order_line.filtered(
            lambda l: l.product_id.id == store.delivery_product_id.id
        )
        if existing_ship_lines:
            return
        shipping_line_vals = []
        for ship in shipping_lines:
            shipping_line_vals.append(
                {
                    "order_id": order.id,
                    "product_id": store.delivery_product_id.id,
                    "name": ship.get("title") or _("Shipping"),
                    "product_uom_qty": 1.0,
                    "price_unit": float_round(float(ship.get("price") or 0.0), 2),
                }
            )
        if shipping_line_vals:
            self.env["sale.order.line"].create(shipping_line_vals)

    def create_order_from_payload(self, payload, store=None):
        SaleOrder = self.env["sale.order"]
        Partner = self.env["res.partner"]
        ProductProduct = self.env["product.product"]
        VariantMap = self.env["shopify.variant.map"]

        shopify_order_id = payload.get("id")
        existing = self._find_existing_order(payload, store)
        if existing:
            shopify_user_id = self._resolve_shopify_user_id(store)
            if shopify_user_id and existing.user_id.id != shopify_user_id:
                existing.write({"user_id": shopify_user_id})
            if store and (store.order_edit_sync_mode or "draft_sent") != "ignore":
                from .order_update_service import OrderUpdateService

                OrderUpdateService(self.env, import_service=self.import_service).update_order_from_payload(
                    existing, payload, store
                )
            return existing

        customer = payload.get("customer") or {}
        billing = payload.get("billing_address") or {}
        shipping = payload.get("shipping_address") or {}

        partner, partner_invoice, partner_shipping = self._get_or_create_partner(
            payload, customer, billing, shipping, store
        )

        order_vals = self._build_order_vals(payload, store, partner)
        order = SaleOrder.create(order_vals)

        self._ensure_order_mapping(store, shopify_order_id, order)

        line_items = self.prepare_line_items_with_discounts(payload)
        variant_map_by_id, product_by_variant_id, product_by_sku = self._prepare_line_product_lookups(
            store, line_items
        )

        missing_variant_map_vals = []
        order_line_vals = []
        for item in line_items:
            quantity = float(item.get("quantity") or 0.0)
            price = float(item.get("price") or 0.0)
            product = self._resolve_line_product(
                item, store, variant_map_by_id, product_by_variant_id, product_by_sku
            )
            vals_map = self._build_missing_variant_map_vals(item, store, product, variant_map_by_id)
            if vals_map:
                missing_variant_map_vals.append(vals_map)

            order_line_vals.append(self._build_order_line_vals(order, item, store, product))

        if missing_variant_map_vals:
            VariantMap.create(missing_variant_map_vals)
        if order_line_vals:
            self.env["sale.order.line"].create(order_line_vals)

        self._create_shipping_lines(order, store, payload)

        return order

    def _get_or_create_partner(self, payload, customer, billing, shipping, store):
        Partner = self.env["res.partner"]

        # Fallback to default POS customer when there is no customer on a POS order
        if not customer and store and payload.get("source_name") == "pos":
            if store.default_pos_customer_id:
                partner = store.default_pos_customer_id
                return partner, partner, partner

        shopify_customer_id = str((customer or {}).get("id") or "")
        email = (customer.get("email") or payload.get("email") or payload.get("contact_email") or "").strip()
        phone = (customer.get("phone") or payload.get("phone") or "").strip()
        first_name = customer.get("first_name") or ""
        last_name = customer.get("last_name") or ""
        name = (first_name + " " + last_name).strip() or email or _("Shopify Customer")

        partner = False
        if shopify_customer_id:
            partner = Partner.search(
                [("shopify_customer_id", "=", shopify_customer_id), ("parent_id", "=", False)],
                limit=1,
            )
        if not partner and email:
            partner = Partner.search([("email", "=", email), ("parent_id", "=", False)], limit=1)
        if not partner and phone:
            partner = Partner.search([("phone", "=", phone), ("parent_id", "=", False)], limit=1)
        if partner and partner.parent_id:
            partner = partner.commercial_partner_id

        vals = {
            "name": name,
            "shopify_customer_id": shopify_customer_id or False,
        }
        if email:
            vals["email"] = email
        if phone:
            vals["phone"] = phone

        if billing:
            if billing.get("address1"):
                vals["street"] = billing.get("address1")
            if billing.get("address2"):
                vals["street2"] = billing.get("address2")
            if billing.get("city"):
                vals["city"] = billing.get("city")
            if billing.get("zip"):
                vals["zip"] = billing.get("zip")
            country_id = self._get_country_id(billing.get("country_code"))
            if country_id:
                vals["country_id"] = country_id
            state_id = self._get_state_id(
                billing.get("province_code"),
                billing.get("country_code"),
            )
            if state_id:
                vals["state_id"] = state_id

        if partner:
            partner.write(vals)
        else:
            partner = Partner.create(vals)

        # Create / update child contacts for invoice and delivery addresses
        partner_invoice = partner
        partner_shipping = partner

        if billing:
            partner_invoice = self._get_or_create_child_contact(
                partner, billing, "invoice", name or _("Invoice Address")
            )
        if shipping:
            same_shipping = (
                (shipping.get("address1") or "") == (billing.get("address1") or "")
                and (shipping.get("address2") or "") == (billing.get("address2") or "")
                and (shipping.get("city") or "") == (billing.get("city") or "")
                and (shipping.get("zip") or "") == (billing.get("zip") or "")
                and (shipping.get("country_code") or "") == (billing.get("country_code") or "")
                and (shipping.get("province_code") or "") == (billing.get("province_code") or "")
            )
            if same_shipping:
                partner_shipping = partner
            else:
                partner_shipping = self._get_or_create_child_contact(
                    partner, shipping, "delivery", name or _("Shipping Address")
                )

        return partner, partner_invoice, partner_shipping

    def _get_country_id(self, country_code):
        if not country_code:
            return False
        country = self.env["res.country"].search(
            [("code", "=", country_code)], limit=1
        )
        return country.id or False

    def _get_state_id(self, state_code, country_code):
        if not state_code or not country_code:
            return False
        country = self.env["res.country"].search(
            [("code", "=", country_code)], limit=1
        )
        if not country:
            return False
        state = self.env["res.country.state"].search(
            [("code", "=", state_code), ("country_id", "=", country.id)],
            limit=1,
        )
        return state.id or False

    def _get_or_create_child_contact(self, parent, address, contact_type, default_name):
        Partner = self.env["res.partner"]
        vals = {
            "parent_id": parent.id,
            "type": contact_type,
            "name": default_name,
            "street": address.get("address1"),
            "street2": address.get("address2"),
            "city": address.get("city"),
            "zip": address.get("zip"),
            "country_id": self._get_country_id(address.get("country_code")),
            "state_id": self._get_state_id(
                address.get("province_code"), address.get("country_code")
            ),
        }
        exact_domain = [
            ("parent_id", "=", parent.id),
            ("type", "=", contact_type),
            ("street", "=", vals.get("street") or False),
            ("street2", "=", vals.get("street2") or False),
            ("city", "=", vals.get("city") or False),
            ("zip", "=", vals.get("zip") or False),
            ("country_id", "=", vals.get("country_id") or False),
            ("state_id", "=", vals.get("state_id") or False),
        ]
        existing = Partner.search(exact_domain, limit=1)
        if not existing:
            existing = Partner.search(
                [
                    ("parent_id", "=", parent.id),
                    ("type", "=", contact_type),
                ],
                limit=1,
            )
        if existing:
            existing.write(vals)
            return existing
        return Partner.create(vals)

