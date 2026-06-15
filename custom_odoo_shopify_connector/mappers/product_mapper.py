import json
import logging
from odoo import _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ProductMapper:
    """Mapper for transforming Odoo products into Shopify product payloads."""

    def __init__(self, env):
        self.env = env

    @staticmethod
    def _to_price_string(value):
        try:
            amount = float(value or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        return "%.2f" % amount

    @staticmethod
    def _to_int(value, default=0):
        """Safely convert values like '2.0' or 2.0 to int(2).

        Shopify/Odoo payloads sometimes carry numeric values as strings.
        """
        try:
            if value in (None, False, ""):
                return default
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return default
                # Handle float-like strings: "2.0" -> int(2)
                if "." in s:
                    return int(float(s))
                return int(s)
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    def _resolve_description(self, product_tmpl, layer=None, export_description=True):
        if layer and layer.description_override:
            return layer.description_override
        if export_description:
            return (
                product_tmpl.description_sale
                or product_tmpl.description
                or product_tmpl.name
                or ""
            )
        return ""

    def _resolve_title(self, product_tmpl, export_name=True):
        title = product_tmpl.name if export_name else ""
        return (title or "").strip() or _("Product")

    def _resolve_tags(self, product_tmpl, export_tags=True):
        if not export_tags or not product_tmpl.product_tag_ids:
            return ""
        return ",".join(product_tmpl.product_tag_ids.mapped("name"))

    def _resolve_product_type(self, product_tmpl, export_categories=True):
        """Resolve product category for Shopify product_type field."""
        import logging
        _logger = logging.getLogger(__name__)
        
        if not export_categories:
            _logger.info("Category export skipped: export_categories=False for %s", product_tmpl.name)
            return ""
        
        if not product_tmpl.categ_id:
            _logger.warning("Category export skipped: product %s has no category", product_tmpl.name)
            return ""
        
        category_name = product_tmpl.categ_id.name or ""
        if not category_name.strip():
            _logger.warning("Category export skipped: product %s category has empty name", product_tmpl.name)
            return ""
        
        _logger.info("Category exported for %s: %s", product_tmpl.name, category_name.strip())
        return category_name.strip()

    def _resolve_price_value(self, variant, store, export_price=True):
        if export_price:
            pricelist = store.shopify_pricelist_id or store.pricelist_id
            if pricelist:
                return pricelist._get_product_price(variant, 1.0)
        return variant.lst_price or variant.list_price or 0.0

    def _calculate_inventory_quantity(self, variant, store):
        """Calculate inventory quantity using the same logic as stock service."""
        stock_type = store.shopify_stock_type or "free_qty"
        
        if store.shopify_location_id:
            # Use with_context to limit quantities to the specific location
            variant_ctx = variant.with_context(location=store.shopify_location_id)
            qty_available = variant_ctx.qty_available
            reserved = getattr(variant_ctx, "reserved_quantity", 0.0)
            outgoing = getattr(variant_ctx, "outgoing_qty", 0.0)
            incoming = getattr(variant_ctx, "incoming_qty", 0.0)
        else:
            qty_available = variant.qty_available
            reserved = getattr(variant, "reserved_quantity", 0.0)
            outgoing = getattr(variant, "outgoing_qty", 0.0)
            incoming = getattr(variant, "incoming_qty", 0.0)

        if stock_type == "forecast_qty":
            return qty_available - outgoing + incoming
        # default: free to use
        return qty_available - reserved

    def _map_variant(self, variant, store, export_price=True, shopify_variant_id=False):
        sku = (variant.default_code or "").strip()
        if not sku:
            sku = "SHP-%s-%s" % (variant.product_tmpl_id.id, variant.id)
            variant.default_code = sku

        # Calculate inventory using the same logic as stock service
        inventory_quantity = self._calculate_inventory_quantity(variant, store)

        variant_payload = {
            "price": self._to_price_string(
                self._resolve_price_value(variant, store, export_price=export_price)
            ),
            "sku": sku,
            "inventory_quantity": self._to_int(inventory_quantity, default=0),
            "inventory_management": "shopify",
        }
        if shopify_variant_id:
            try:
                variant_payload["id"] = int(shopify_variant_id)
            except (TypeError, ValueError):
                variant_payload["id"] = shopify_variant_id
        return variant_payload

    def _get_variant_option_payload(self, attribute_lines, variant):
        """Build Shopify option1/2/3 fields from Odoo variant attributes."""
        option_payload = {}
        if not attribute_lines:
            return option_payload

        for idx, line in enumerate(attribute_lines, start=1):
            option_key = "option%s" % idx
            value_name = False
            for ptav in variant.product_template_attribute_value_ids:
                if ptav.attribute_line_id.id == line.id:
                    value_name = ptav.name
                    break
            if value_name:
                option_payload[option_key] = value_name
        # Shopify expects option1 when options exist on the template.
        if attribute_lines and "option1" not in option_payload:
            option_payload["option1"] = "Default Title"
        return option_payload

    def _get_variant_attribute_lines(self, product_tmpl):
        """Return variant-generating attribute lines only (max 3 for Shopify)."""
        return product_tmpl.attribute_line_ids.filtered(
            lambda l: getattr(l.attribute_id, "create_variant", "always") != "no_variant"
        ).sorted(key=lambda l: (l.sequence, l.id))[:3]

    def _build_options_payload(self, variants):
        """Build top-level Shopify product options from actual variant payloads."""
        options = []
        for idx in range(1, 4):
            option_key = "option%s" % idx
            values = []
            seen = set()
            for variant in variants:
                val = (variant.get(option_key) or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    values.append(val)
            if values:
                options.append({"name": "Option %s" % idx, "values": values})
        return options

    def _build_image_payload(self, product_tmpl, export_image=True):
        """Build Shopify image payload from product template image."""
        import logging
        _logger = logging.getLogger(__name__)
        
        _logger.info("Building image payload for %s: export_image=%s, has_image=%s",
                     product_tmpl.name, export_image, bool(product_tmpl.image_1920))
        
        if not export_image:
            _logger.info("Image export skipped: export_image=False")
            return []
        if not product_tmpl.image_1920:
            _logger.info("Image export skipped: no image_1920 data")
            return []
        try:
            # image_1920 is already base64-encoded in Odoo, just ensure it's a string
            if isinstance(product_tmpl.image_1920, bytes):
                image_data = product_tmpl.image_1920.decode('utf-8')
            else:
                image_data = product_tmpl.image_1920
            _logger.info("Image payload built: size=%s chars", len(image_data))
            return [{"attachment": image_data}]
        except Exception as e:
            _logger.error("Image encoding failed: %s", str(e))
            return []

    def build_export_product_data(
        self,
        product_tmpl,
        store,
        layer=None,
        export_name=True,
        export_description=True,
        export_tags=True,
        export_categories=True,
        export_price=True,
        export_image=True,
        publish_option="web_only",
    ):
        """Build Shopify product data (without top-level request wrapper)."""
        attribute_lines = self._get_variant_attribute_lines(product_tmpl)
        VariantMap = self.env["shopify.variant.map"]
        variants_rs = product_tmpl.with_context(active_test=False).product_variant_ids.sorted(
            key=lambda p: p.id
        )
        variant_maps = VariantMap.search(
            [
                ("store_id", "=", store.id),
                ("product_id", "in", variants_rs.ids),
            ]
        )
        variant_map_by_product = {vm.product_id.id: vm for vm in variant_maps if vm.product_id}
        variants = []
        for variant in variants_rs:
            vm = variant_map_by_product.get(variant.id)
            variant_payload = self._map_variant(
                variant,
                store,
                export_price=export_price,
                shopify_variant_id=vm.shopify_variant_id if vm else False,
            )
            variant_payload.update(self._get_variant_option_payload(attribute_lines, variant))
            variants.append(variant_payload)

        _logger.info(
            "Export variants count=%s SKUs=%s",
            len(variants),
            [v.get("sku") for v in variants],
        )

        status = "draft" if publish_option == "not_published" else "active"
        vendor = (store.name or "").strip()
        if not vendor or vendor.strip().lower() == "odoobot":
            vendor = "Shopify"
        _logger.info("++++++++=========== NEW DEBUG START HERE ===========++++++++")
        _logger.info("Export payload prep product=%s (id=%s)", product_tmpl.display_name, product_tmpl.id)
        _logger.info("Resolved vendor=%s", (vendor or "")[:100])
        _logger.info("++++++++=========== NEW DEBUG END HERE ===========++++++++")
        product_data = {
            "title": self._resolve_title(product_tmpl, export_name=export_name),
            "body_html": self._resolve_description(
                product_tmpl,
                layer=layer,
                export_description=export_description,
            ),
            "vendor": vendor,
            "product_type": self._resolve_product_type(
                product_tmpl, export_categories=export_categories
            ),
            "variants": variants,
            "status": status,
        }

        options = self._build_options_payload(variants)
        if options:
            product_data["options"] = options

        # Add images if available
        images = self._build_image_payload(product_tmpl, export_image=export_image)
        if images:
            product_data["images"] = images

        tags = self._resolve_tags(product_tmpl, export_tags=export_tags)
        if tags:
            product_data["tags"] = tags

        # Remove only None values; keep empty strings where accepted by Shopify.
        return {k: v for k, v in product_data.items() if v is not None}

    def validate_export_product_data(self, product_data, product_name="Product"):
        """Validate and normalize Shopify product payload before API call."""
        if not isinstance(product_data, dict) or not product_data:
            raise ValidationError(_("Export payload is empty for product '%s'.") % product_name)

        title = (product_data.get("title") or "").strip()
        if not title:
            product_data["title"] = product_name or _("Product")

        variants = product_data.get("variants")
        if not isinstance(variants, list) or not variants:
            raise ValidationError(
                _("Product '%s' has no variants to export.") % product_name
            )

        for variant in variants:
            if not isinstance(variant, dict):
                raise ValidationError(_("Invalid variant payload for product '%s'.") % product_name)
            price = variant.get("price")
            if price in (None, "", False):
                variant["price"] = "0.00"
            else:
                variant["price"] = self._to_price_string(price)
            if "inventory_quantity" not in variant:
                variant["inventory_quantity"] = 0
            else:
                variant["inventory_quantity"] = self._to_int(
                    variant.get("inventory_quantity"), default=0
                )

        return product_data

    def _field_exists(self, product_tmpl, field_name):
        return field_name in (product_tmpl._fields or {})

    def _m2m_names(self, records):
        if hasattr(records, "mapped"):
            return [name for name in records.mapped("name") if name]
        return [getattr(rec, "name", False) for rec in (records or []) if getattr(rec, "name", False)]

    @staticmethod
    def _to_b64_text(value):
        if not value:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    @staticmethod
    def _ts_key(field_name):
        return "ts_%s" % field_name

    def build_export_metafields(self, product_tmpl):
        """Build Shopify metafields for optional custom addon fields."""
        metafields = []
        namespace = "odoo_custom"

        write_date_value = None
        if self._field_exists(product_tmpl, "write_date") and product_tmpl.write_date:
            write_date_value = product_tmpl.write_date.isoformat()

        def add_metafield(key, value, mtype="single_line_text_field"):
            if value in (None, False, ""):
                return
            if isinstance(value, str) and len(value) > 65536:
                _logger.warning(
                    "Skipping oversized metafield %s (length=%s exceeds Shopify limit)",
                    key,
                    len(value),
                )
                return
            metafields.append(
                {
                    "namespace": namespace,
                    "key": key,
                    "type": mtype,
                    "value": value,
                }
            )
            if write_date_value and not key.startswith("ts_"):
                metafields.append(
                    {
                        "namespace": namespace,
                        "key": self._ts_key(key),
                        "type": "date_time",
                        "value": write_date_value,
                    }
                )

        # last-updated-wins companion marker on Shopify side
        if write_date_value:
            add_metafield(
                "odoo_write_date",
                write_date_value,
                "date_time",
            )

        if self._field_exists(product_tmpl, "brand_id") and product_tmpl.brand_id:
            add_metafield("brand", product_tmpl.brand_id.name)
        if self._field_exists(product_tmpl, "type_id") and product_tmpl.type_id:
            add_metafield("product_type_custom", product_tmpl.type_id.name)
        if self._field_exists(product_tmpl, "collection_ids"):
            names = self._m2m_names(product_tmpl.collection_ids)
            if names:
                add_metafield("collections", json.dumps(names), "json")
        if self._field_exists(product_tmpl, "hair_type_ids"):
            names = self._m2m_names(product_tmpl.hair_type_ids)
            if names:
                add_metafield("hair_types", json.dumps(names), "json")

        html_fields = [
            "benefits",
            "description_long",
            "application",
            "ingredients",
            "inci_list",
            "block1_text",
            "block2_text",
        ]
        for field_name in html_fields:
            if self._field_exists(product_tmpl, field_name):
                add_metafield(field_name, product_tmpl[field_name], "multi_line_text_field")

        char_fields = [
            "meta_title",
            "meta_description",
            "block1_title",
            "block2_title",
        ]
        for field_name in char_fields:
            if self._field_exists(product_tmpl, field_name):
                add_metafield(field_name, product_tmpl[field_name], "single_line_text_field")

        if self._field_exists(product_tmpl, "cross_sell_ids"):
            cross_sell = [
                {"id": p.id, "name": p.name}
                for p in product_tmpl.cross_sell_ids
            ]
            if cross_sell:
                add_metafield("cross_sell", json.dumps(cross_sell), "json")

        if self._field_exists(product_tmpl, "similar_product_ids"):
            similar = [
                {"id": p.id, "name": p.name}
                for p in product_tmpl.similar_product_ids
            ]
            if similar:
                add_metafield("similar_products", json.dumps(similar), "json")

        if self._field_exists(product_tmpl, "block1_image"):
            block1_image_b64 = self._to_b64_text(product_tmpl.block1_image)
            if block1_image_b64:
                add_metafield("block1_image_b64", block1_image_b64, "multi_line_text_field")

        if self._field_exists(product_tmpl, "block2_image"):
            block2_image_b64 = self._to_b64_text(product_tmpl.block2_image)
            if block2_image_b64:
                add_metafield("block2_image_b64", block2_image_b64, "multi_line_text_field")

        # Extend with custom namespace metafields built on product.template, if available.
        # Do NOT replace existing logic; just append safely.
        try:
            _logger.info("++++++++=========== NEW DEBUG START HERE ===========++++++++")
            _logger.info("Existing metafields count: %s", len(metafields))
            existing_keys = {
                (m.get("namespace"), m.get("key"))
                for m in metafields
                if isinstance(m, dict) and m.get("namespace") and m.get("key")
            }
            _logger.info("Existing keys count: %s", len(existing_keys))
            if hasattr(product_tmpl, "_prepare_shopify_metafields"):
                extra = product_tmpl._prepare_shopify_metafields() or []
                extra_keys_preview = []

                for mf in extra:
                    if not isinstance(mf, dict):
                        continue

                    if not mf.get("namespace") or not mf.get("key"):
                        continue
                    if len(extra_keys_preview) < 50:
                        extra_keys_preview.append(mf.get("key"))

                    dedup_key = (mf.get("namespace"), mf.get("key"))

                    if dedup_key in existing_keys:
                        _logger.info("Skipping duplicate metafield: %s", (mf.get("key") or "")[:100])
                        continue

                    metafields.append(mf)
                    existing_keys.add(dedup_key)
                    _logger.info("Appending metafield: %s", (mf.get("key") or "")[:100])
                _logger.info("Extra metafields from template (keys preview): %s", extra_keys_preview)
            _logger.info("Final metafields count: %s", len(metafields))
            _logger.info("++++++++=========== NEW DEBUG END HERE ===========++++++++")
        except Exception as exc:
            _logger.warning(
                "Failed to prepare custom metafields for product %s: %s",
                getattr(product_tmpl, "display_name", False) or getattr(product_tmpl, "name", ""),
                str(exc),
            )
            _logger.info("++++++++=========== NEW DEBUG END HERE ===========++++++++")

        return metafields

