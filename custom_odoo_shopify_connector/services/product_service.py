import base64
import logging
import json
import hashlib
import time
import re
from datetime import datetime
from datetime import timezone

import requests

from odoo import _, fields
from odoo.exceptions import ValidationError
from ..mappers.product_mapper import ProductMapper
from ..models.license_mixin import license_is_active_strict, trial_batch_limit

_logger = logging.getLogger(__name__)
# region agent log
_DEBUG_LOG_PATH = "/home/kali/Downloads/custom_odoo_shopify_connector/.cursor/debug-dc140e.log"


def _agent_log(payload):
    try:
        payload = dict(payload or {})
        payload.setdefault("sessionId", "dc140e")
        payload.setdefault("timestamp", int(__import__("time").time() * 1000))
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass


# endregion agent log


class ProductService:
    """Business logic for importing and updating Shopify products and variants."""

    def __init__(self, env):
        self.env = env

    def _to_float(self, value, default=0.0):
        """Parse Shopify numeric values defensively without crashing import."""
        if value in (None, False, ""):
            return float(default)
        try:
            return float(value)
        except (TypeError, ValueError):
            text = str(value).strip().replace(",", "")
            match = re.search(r"-?\d+(?:\.\d+)?", text)
            if match:
                try:
                    return float(match.group(0))
                except (TypeError, ValueError):
                    pass
        return float(default)

    def _get_or_create_category(self, product_type):
        """Get or create product.category from Shopify product_type."""
        if not product_type or not str(product_type).strip():
            return self.env["product.category"]
        ProductCategory = self.env["product.category"]
        category = ProductCategory.search([("name", "=", product_type.strip())], limit=1)
        if not category:
            category = ProductCategory.create({"name": product_type.strip()})
        return category

    def _get_or_create_tags(self, tags_string):
        """Parse Shopify tags (comma-separated) and return product.tag recordset."""
        if not tags_string or not str(tags_string).strip():
            return self.env["product.tag"]
        ProductTag = self.env["product.tag"]
        names = [t.strip() for t in str(tags_string).split(",") if t.strip()]
        tag_ids = []
        for name in names:
            tag = ProductTag.search([("name", "=", name)], limit=1)
            if not tag:
                tag = ProductTag.create({"name": name})
            tag_ids.append(tag.id)
        return ProductTag.browse(tag_ids)

    def _download_image_as_base64(self, image_url):
        """Download image from URL and return base64-encoded string, or False on failure."""
        if not image_url or not str(image_url).strip():
            return False
        image_url = str(image_url).strip()
        try:
            headers = {
                "User-Agent": "Odoo-Shopify-Connector/1.0",
                "Accept": "image/*,*/*",
            }
            response = requests.get(
                image_url,
                headers=headers,
                timeout=15,
                allow_redirects=True,
            )
            response.raise_for_status()
            content = response.content
            if not content:
                _logger.warning("Empty response for Shopify image: %s", image_url)
                return False
            return base64.b64encode(content).decode("ascii")
        except Exception as e:
            _logger.warning("Failed to download Shopify image %s: %s", image_url, e)
            return False

    def _extract_images(self, payload):
        images = payload.get("images") or []
        # Some API responses use singular "image" for the main image
        if not images and payload.get("image"):
            images = [payload["image"]] if isinstance(payload.get("image"), dict) else []
        return images

    def _find_or_create_template(self, title, product_map):
        ProductTemplate = self.env["product.template"]
        template = product_map.product_tmpl_id if product_map else False
        if not template:
            template = ProductTemplate.search([("name", "=", title)], limit=1)
        return template

    def _find_template_by_sku(self, sku):
        sku = (sku or "").strip()
        if not sku:
            return self.env["product.template"]
        product = self.env["product.product"].search(
            [("default_code", "=", sku)],
            limit=1,
        )
        return product.product_tmpl_id if product else self.env["product.template"]

    def _build_template_vals(self, payload, store, categ, tag_ids):
        title = payload.get("title") or ""
        description = payload.get("body_html") or ""
        variants = payload.get("variants") or []

        template_vals = {
            "name": title or _("Shopify Product"),
            "type": "consu",
            # Guard against DB-level defaults injecting duplicate attribute lines
            # on create (can trigger product_product_combination_unique).
            "attribute_line_ids": [(5, 0, 0)],
            # Odoo 19.0 no longer has the `track_inventory` field on product.template.
            # Stock behavior is controlled via standard inventory configuration instead.
            "description": description,
        }
        if getattr(store, "import_sales_description", True):
            template_vals["description_sale"] = description or False
        if categ:
            template_vals["categ_id"] = categ.id
        if tag_ids:
            template_vals["product_tag_ids"] = [(6, 0, tag_ids.ids)]

        # Price is finalized after variant import using Odoo tax engine and
        # PTAV price_extra; keep a safe placeholder at template creation.
        template_vals["list_price"] = 0.0

        # Weight from first variant if available
        if variants and variants[0].get("weight"):
            try:
                template_vals["weight"] = float(variants[0].get("weight") or 0)
            except (TypeError, ValueError):
                pass

        return template_vals

    def _parse_shopify_datetime(self, dt_value):
        if not dt_value:
            return None
        value = str(dt_value).strip()
        if not value:
            return None
        try:
            value = value.replace("Z", "+00:00")
            return self._normalize_datetime_for_compare(datetime.fromisoformat(value))
        except Exception:
            return None

    def _normalize_datetime_for_compare(self, dt_value):
        """Return a naive UTC datetime for safe comparisons."""
        if not dt_value:
            return None
        if isinstance(dt_value, str):
            parsed = self._parse_shopify_datetime(dt_value)
            if parsed:
                return parsed
            return None
        dt_obj = dt_value
        if getattr(dt_obj, "tzinfo", None):
            dt_obj = dt_obj.astimezone(timezone.utc).replace(tzinfo=None)
        return dt_obj

    def _extract_metafield_timestamp(self, mf_record, ts_value=None):
        if ts_value:
            parsed = self._parse_shopify_datetime(ts_value)
            if parsed:
                return parsed
        if isinstance(mf_record, dict):
            for key in ("updated_at", "created_at"):
                parsed = self._parse_shopify_datetime(mf_record.get(key))
                if parsed:
                    return parsed
        return fields.Datetime.now()

    def _get_field_sync_state(self, store, template, field_name):
        return self.env["shopify.product.field.state"].sudo().search(
            [
                ("store_id", "=", store.id),
                ("product_tmpl_id", "=", template.id),
                ("field_name", "=", field_name),
            ],
            limit=1,
        )

    def _mark_field_sync_state(
        self, store, template, field_name, source, local_updated_at=None, remote_updated_at=None
    ):
        if not template:
            return
        State = self.env["shopify.product.field.state"].sudo()
        state = self._get_field_sync_state(store, template, field_name)
        vals = {
            "store_id": store.id,
            "product_tmpl_id": template.id,
            "field_name": field_name,
            "last_source": source,
        }
        if local_updated_at:
            vals["local_updated_at"] = local_updated_at
        if remote_updated_at:
            vals["remote_updated_at"] = remote_updated_at
        if state:
            state.write(vals)
        else:
            State.create(vals)

    def _should_apply_remote_field(self, store, template, field_name, remote_updated_at):
        state = self._get_field_sync_state(store, template, field_name)
        if not state:
            return True
        if not remote_updated_at:
            return True
        local_updated = self._normalize_datetime_for_compare(state.local_updated_at)
        remote_updated = self._normalize_datetime_for_compare(remote_updated_at)
        if local_updated and remote_updated and local_updated > remote_updated:
            print(
                "[shopify-sync] skip remote field=%s product=%s reason=local_newer local=%s remote=%s"
                % (field_name, template.display_name, local_updated, remote_updated)
            )
            return False
        return True

    def _should_import_based_on_timestamp(self, template, payload):
        """Last-updated-wins: skip Shopify import when Odoo is newer."""
        if not template or not template.exists():
            return True
        shopify_updated = self._normalize_datetime_for_compare(
            self._parse_shopify_datetime(payload.get("updated_at"))
        )
        if not shopify_updated:
            return True
        odoo_updated = self._normalize_datetime_for_compare(template.write_date)
        if not odoo_updated:
            return True
        try:
            return shopify_updated >= odoo_updated
        except Exception:
            return True

    def _field_exists(self, record, field_name):
        return field_name in (record._fields or {})

    def _get_or_create_named_record(self, model_name, value):
        if not value:
            return False
        Model = self.env[model_name].sudo()
        record = Model.search([("name", "=", value)], limit=1)
        if not record:
            record = Model.create({"name": value})
        return record

    def _safe_json_list(self, value):
        try:
            parsed = json.loads(value or "[]")
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def _resolve_related_products_from_json(self, json_value):
        records = self.env["product.template"]
        for item in self._safe_json_list(json_value):
            if not isinstance(item, dict):
                continue
            rec = False
            if item.get("id"):
                rec = self.env["product.template"].sudo().browse(int(item["id"]))
                if rec and not rec.exists():
                    rec = False
            if not rec and item.get("name"):
                rec = self.env["product.template"].sudo().search(
                    [("name", "=", item["name"])], limit=1
                )
            if rec:
                records |= rec
        return records

    def _apply_custom_metafields_to_template(self, template, store, metafields):
        if not template or not metafields or not store:
            return
        license_ok = license_is_active_strict(self.env)
        if not license_ok:
            metafields = (metafields or [])[: trial_batch_limit() + 3]
            time.sleep(1)
        _logger.info("++++++++=========== META SYNC DEBUG START ===========++++++++")
        mf_by_key = {
            (mf.get("namespace"), mf.get("key")): mf
            for mf in metafields
            if mf.get("namespace") and mf.get("key")
        }
        namespace = "odoo_custom"
        vals = {}

        def pull_value(field_key):
            rec = mf_by_key.get((namespace, field_key)) or {}
            return rec.get("value"), rec

        def should_apply(field_name, mf_record):
            ts_val, _ts_rec = pull_value("ts_%s" % field_name)
            remote_ts = self._extract_metafield_timestamp(mf_record, ts_val)
            return self._should_apply_remote_field(
                store=store,
                template=template,
                field_name=field_name,
                remote_updated_at=remote_ts,
            ), remote_ts

        brand, brand_mf = pull_value("brand")
        if brand and self._field_exists(template, "brand_id"):
            apply_it, remote_ts = should_apply("brand_id", brand_mf)
            if apply_it:
                brand_rec = self._get_or_create_named_record("product.brand", brand)
                if brand_rec:
                    vals["brand_id"] = brand_rec.id
                    self._mark_field_sync_state(
                        store, template, "brand_id", "shopify", remote_updated_at=remote_ts
                    )

        product_type_custom, product_type_mf = pull_value("product_type_custom")
        if product_type_custom and self._field_exists(template, "type_id"):
            apply_it, remote_ts = should_apply("type_id", product_type_mf)
            if apply_it:
                type_rec = self._get_or_create_named_record("product.type", product_type_custom)
                if type_rec:
                    vals["type_id"] = type_rec.id
                    self._mark_field_sync_state(
                        store, template, "type_id", "shopify", remote_updated_at=remote_ts
                    )

        collections_raw, collections_mf = pull_value("collections")
        if collections_raw and self._field_exists(template, "collection_ids"):
            apply_it, remote_ts = should_apply("collection_ids", collections_mf)
            if apply_it:
                names = self._safe_json_list(collections_raw)
                ids = []
                for name in names:
                    rec = self._get_or_create_named_record("product.collection", name)
                    if rec:
                        ids.append(rec.id)
                vals["collection_ids"] = [(6, 0, ids)]
                self._mark_field_sync_state(
                    store, template, "collection_ids", "shopify", remote_updated_at=remote_ts
                )

        hair_types_raw, hair_types_mf = pull_value("hair_types")
        if hair_types_raw and self._field_exists(template, "hair_type_ids"):
            apply_it, remote_ts = should_apply("hair_type_ids", hair_types_mf)
            if apply_it:
                names = self._safe_json_list(hair_types_raw)
                ids = []
                for name in names:
                    rec = self._get_or_create_named_record("hair.type", name)
                    if rec:
                        ids.append(rec.id)
                vals["hair_type_ids"] = [(6, 0, ids)]
                self._mark_field_sync_state(
                    store, template, "hair_type_ids", "shopify", remote_updated_at=remote_ts
                )

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
            value, value_mf = pull_value(field_name)
            if value and self._field_exists(template, field_name):
                apply_it, remote_ts = should_apply(field_name, value_mf)
                if apply_it:
                    vals[field_name] = value
                    self._mark_field_sync_state(
                        store, template, field_name, "shopify", remote_updated_at=remote_ts
                    )

        char_fields = [
            "meta_title",
            "meta_description",
            "block1_title",
            "block2_title",
        ]
        for field_name in char_fields:
            value, value_mf = pull_value(field_name)
            if value and self._field_exists(template, field_name):
                apply_it, remote_ts = should_apply(field_name, value_mf)
                if apply_it:
                    vals[field_name] = value
                    self._mark_field_sync_state(
                        store, template, field_name, "shopify", remote_updated_at=remote_ts
                    )

        cross_sell_raw, cross_sell_mf = pull_value("cross_sell")
        if cross_sell_raw and self._field_exists(template, "cross_sell_ids"):
            apply_it, remote_ts = should_apply("cross_sell_ids", cross_sell_mf)
            if apply_it:
                cross_sell_records = self._resolve_related_products_from_json(cross_sell_raw)
                vals["cross_sell_ids"] = [(6, 0, cross_sell_records.ids)]
                self._mark_field_sync_state(
                    store, template, "cross_sell_ids", "shopify", remote_updated_at=remote_ts
                )

        similar_raw, similar_mf = pull_value("similar_products")
        if similar_raw and self._field_exists(template, "similar_product_ids"):
            apply_it, remote_ts = should_apply("similar_product_ids", similar_mf)
            if apply_it:
                similar_records = self._resolve_related_products_from_json(similar_raw)
                vals["similar_product_ids"] = [(6, 0, similar_records.ids)]
                self._mark_field_sync_state(
                    store, template, "similar_product_ids", "shopify", remote_updated_at=remote_ts
                )

        block1_image_raw, block1_image_mf = pull_value("block1_image_b64")
        if block1_image_raw and self._field_exists(template, "block1_image"):
            apply_it, remote_ts = should_apply("block1_image", block1_image_mf)
            if apply_it:
                vals["block1_image"] = block1_image_raw
                self._mark_field_sync_state(
                    store, template, "block1_image", "shopify", remote_updated_at=remote_ts
                )

        block2_image_raw, block2_image_mf = pull_value("block2_image_b64")
        if block2_image_raw and self._field_exists(template, "block2_image"):
            apply_it, remote_ts = should_apply("block2_image", block2_image_mf)
            if apply_it:
                vals["block2_image"] = block2_image_raw
                self._mark_field_sync_state(
                    store, template, "block2_image", "shopify", remote_updated_at=remote_ts
                )

        if vals:
            print(
                "[shopify-sync] applying custom fields from Shopify product=%s fields=%s"
                % (template.display_name, sorted(vals.keys()))
            )
            template.write(vals)

        # Apply custom namespace metafields (Shopify -> Odoo) by key name.
        try:
            for mf in metafields or []:
                if not license_ok:
                    time.sleep(1)
                if not isinstance(mf, dict):
                    continue
                if mf.get("namespace") != "custom":
                    continue
                field_name = mf.get("key")
                value = mf.get("value")
                mf_type = mf.get("type")
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "IMP_ENTRY",
                        "location": "services/product_service.py:_apply_custom_metafields_to_template:entry",
                        "message": "import_meta_entry",
                        "data": {
                            "field": field_name,
                            "mf_type": mf_type,
                            "value_py_type": type(value).__name__,
                            "value_preview": str(value)[:160] if value is not None else None,
                        },
                    }
                )
                # endregion agent log
                _logger.info(
                    "[IMPORT META] Field=%s Value=%s Type=%s",
                    field_name,
                    str(value)[:100],
                    mf_type,
                )
                if not field_name:
                    continue

                # --------------------------------------------------
                # STRICT NORMALIZATION (Shopify → Odoo)
                # --------------------------------------------------

                # JSON → decode
                if mf_type == "json":
                    try:
                        value = json.loads(value)
                        # region agent log
                        _agent_log(
                            {
                                "runId": "pre-fix",
                                "hypothesisId": "IMP_JSON",
                                "location": "services/product_service.py:_apply_custom_metafields_to_template:json_loads_ok",
                                "message": "import_meta_branch",
                                "data": {
                                    "field": field_name,
                                    "branch": "json_loads_ok",
                                    "decoded_type": type(value).__name__,
                                },
                            }
                        )
                        # endregion agent log
                    except Exception:
                        # region agent log
                        _agent_log(
                            {
                                "runId": "pre-fix",
                                "hypothesisId": "IMP_JSON",
                                "location": "services/product_service.py:_apply_custom_metafields_to_template:json_loads_fail",
                                "message": "import_meta_exception",
                                "data": {"field": field_name, "branch": "json_loads_fail"},
                            }
                        )
                        # endregion agent log
                        pass

                # BOOLEAN
                if value in ("true", "false"):
                    value = True if value == "true" else False
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "IMP_BOOL",
                            "location": "services/product_service.py:_apply_custom_metafields_to_template:boolean",
                            "message": "import_meta_branch",
                            "data": {"field": field_name, "branch": "boolean", "value": value},
                        }
                    )
                    # endregion agent log

                # NUMBER
                if isinstance(value, str) and value.replace(".", "", 1).isdigit():
                    value = float(value)
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "IMP_NUM",
                            "location": "services/product_service.py:_apply_custom_metafields_to_template:number",
                            "message": "import_meta_branch",
                            "data": {"field": field_name, "branch": "number", "value": value},
                        }
                    )
                    # endregion agent log

                # LIST JSON → convert to IDs if needed
                if isinstance(value, list):
                    try:
                        value = [(6, 0, [int(v) for v in value if str(v).isdigit()])]
                        # region agent log
                        _agent_log(
                            {
                                "runId": "pre-fix",
                                "hypothesisId": "IMP_LIST",
                                "location": "services/product_service.py:_apply_custom_metafields_to_template:list_m2m_ok",
                                "message": "import_meta_branch",
                                "data": {
                                    "field": field_name,
                                    "branch": "list_m2m_ok",
                                    "ids_len": len(value[0][2]) if value and value[0] and len(value[0]) > 2 else 0,
                                },
                            }
                        )
                        # endregion agent log
                    except Exception:
                        # region agent log
                        _agent_log(
                            {
                                "runId": "pre-fix",
                                "hypothesisId": "IMP_LIST",
                                "location": "services/product_service.py:_apply_custom_metafields_to_template:list_m2m_fail",
                                "message": "import_meta_exception",
                                "data": {"field": field_name, "branch": "list_m2m_fail"},
                            }
                        )
                        # endregion agent log
                        pass
                # FILE reference → keep as is
                elif mf_type == "file_reference":
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "IMP_FILE_REF",
                            "location": "services/product_service.py:_apply_custom_metafields_to_template:file_reference",
                            "message": "import_meta_branch",
                            "data": {"field": field_name, "branch": "file_reference"},
                        }
                    )
                    # endregion agent log
                    pass

                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "IMP_EXIT",
                        "location": "services/product_service.py:_apply_custom_metafields_to_template:pre_write",
                        "message": "import_meta_pre_write",
                        "data": {
                            "field": field_name,
                            "final_py_type": type(value).__name__,
                            "final_preview": str(value)[:160] if value is not None else None,
                            "will_write": bool(field_name and hasattr(template, field_name)),
                        },
                    }
                )
                # endregion agent log
                if hasattr(template, field_name):
                    template.write({field_name: value})
                else:
                    _logger.warning(
                        "[IMPORT META WARNING] Field not found on product.template: %s",
                        field_name,
                    )
        finally:
            _logger.info("++++++++=========== META SYNC DEBUG END ===========++++++++")

    def _upsert_template(self, template, template_vals):
        ProductTemplate = self.env["product.template"]
        if template:
            template.write(template_vals)
            return template
        # Some databases/modules inject product defaults that can trigger
        # duplicate variant-combination rows at template create time.
        # Isolate create and gracefully recover by reusing an existing template.
        try:
            with self.env.cr.savepoint():
                return ProductTemplate.create(template_vals)
        except Exception as e:
            message = str(e or "")
            if "product_product_combination_unique" not in message:
                raise

        fallback_name = template_vals.get("name")
        if fallback_name:
            existing = ProductTemplate.search([("name", "=", fallback_name)], limit=1)
            if existing:
                existing.write(template_vals)
                return existing

        # Re-raise if we cannot safely recover.
        raise

    def _resolve_main_image_url(self, images, variants):
        image_url = None
        if images:
            # Shopify product.images[].src
            image_url = images[0].get("src") or images[0].get("source")
        if not image_url and variants:
            # Fallback: first variant can have an image object (e.g. from API)
            first_variant = variants[0] if isinstance(variants[0], dict) else {}
            variant_image = first_variant.get("image")
            if isinstance(variant_image, dict):
                image_url = variant_image.get("src") or variant_image.get("source")
        return image_url

    def _sync_template_main_image(self, template, store, shopify_product_id, images, variants):
        image_import_state = "pending"
        image_import_message = ""

        if not getattr(store, "sync_product_images", True) or not template:
            return "skipped", "Image sync disabled in store settings."

        image_url = self._resolve_main_image_url(images, variants)
        if not image_url:
            return "skipped", "No image found in Shopify payload."

        b64 = self._download_image_as_base64(image_url)
        if b64:
            template.image_1920 = b64
            _logger.debug(
                "Imported image for product %s (Shopify ID %s)",
                template.name,
                shopify_product_id,
            )
            return "done", "Image imported successfully."

        _logger.warning("Could not download image for product %s: %s", template.name, image_url)
        return "failed", "Could not download image from Shopify URL."

    def _ensure_product_map(self, ProductMap, store, shopify_product_id, template, product_map):
        if product_map:
            return
        ProductMap.create(
            {
                "store_id": store.id,
                "shopify_product_id": str(shopify_product_id),
                "product_tmpl_id": template.id,
            }
        )

    def _build_attribute_value_maps(self, options, variants=None):
        ProductAttribute = self.env["product.attribute"]
        ProductAttributeValue = self.env["product.attribute.value"]

        attribute_map = {}
        value_map = {}
        values_by_position = {}

        for index, option in enumerate(options or [], start=1):
            name = option.get("name")
            position = option.get("position")
            if not position:
                # Some Shopify payloads omit position; keep stable order fallback.
                position = index
            if not name:
                continue
            try:
                position = int(position)
            except (TypeError, ValueError):
                continue

            attribute = ProductAttribute.search([("name", "=", name)], limit=1)
            if not attribute:
                attribute = ProductAttribute.create({"name": name})

            attribute_map[position] = attribute

            option_values = [v for v in (option.get("values") or []) if v not in (None, False, "")]
            if option_values:
                values_by_position.setdefault(position, set()).update(
                    [str(v).strip() for v in option_values if str(v).strip()]
                )

        # Shopify payloads can have incomplete options[].values.
        # Always derive real variant values from each variant optionN field.
        for variant in variants or []:
            if not isinstance(variant, dict):
                continue
            for position, _attribute in attribute_map.items():
                option_key = "option%s" % position
                option_value = (variant.get(option_key) or "").strip()
                if option_value:
                    values_by_position.setdefault(position, set()).add(option_value)

        for position, attribute in attribute_map.items():
            for value_name in sorted(values_by_position.get(position, set())):
                key = (attribute.id, value_name)
                if key in value_map:
                    continue
                value = ProductAttributeValue.search(
                    [("name", "=", value_name), ("attribute_id", "=", attribute.id)],
                    limit=1,
                )
                if not value:
                    value = ProductAttributeValue.create(
                        {"name": value_name, "attribute_id": attribute.id}
                    )
                value_map[key] = value

        return attribute_map, value_map

    def _apply_template_attribute_lines(self, template, attribute_map, value_map):
        if not attribute_map:
            return
        desired_map = {}
        for attribute in attribute_map.values():
            values = sorted(
                [
                    v.id
                    for (attr_id, _), v in value_map.items()
                    if attr_id == attribute.id
                ]
            )
            if values:
                desired_map[attribute.id] = values

        existing_map = {}
        for line in template.attribute_line_ids:
            existing_map[line.attribute_id.id] = sorted(line.value_ids.ids)

        # Avoid destructive reset/recreate when nothing changed.
        if desired_map == existing_map:
            return

        commands = []
        existing_lines_by_attr = {line.attribute_id.id: line for line in template.attribute_line_ids}

        # Remove lines that are no longer present.
        for attr_id, line in existing_lines_by_attr.items():
            if attr_id not in desired_map:
                commands.append((2, line.id, 0))

        # Update existing lines / create missing lines.
        for attr_id, values in desired_map.items():
            line = existing_lines_by_attr.get(attr_id)
            if line:
                if sorted(line.value_ids.ids) != values:
                    commands.append((1, line.id, {"value_ids": [(6, 0, values)]}))
            else:
                commands.append((0, 0, {"attribute_id": attr_id, "value_ids": [(6, 0, values)]}))

        if commands:
            template.write({"attribute_line_ids": commands})

    def _resolve_ptav_ids(self, template, variant_payload, attribute_map, value_map):
        if not attribute_map:
            return []

        ptav_ids = []
        PTAV = self.env["product.template.attribute.value"]
        for position, attribute in attribute_map.items():
            option_key = "option%s" % position
            option_value_name = (variant_payload.get(option_key) or "").strip()
            if not option_value_name:
                continue
            pav = value_map.get((attribute.id, option_value_name))
            if not pav:
                continue
            ptav = PTAV.search(
                [
                    ("product_tmpl_id", "=", template.id),
                    ("product_attribute_value_id", "=", pav.id),
                ],
                limit=1,
            )
            if ptav:
                ptav_ids.append(ptav.id)
        return ptav_ids

    def _find_variant_by_ptav_ids(self, template, ptav_ids):
        if not ptav_ids:
            return self.env["product.product"]
        wanted = set(ptav_ids)
        for variant in template.product_variant_ids:
            if set(variant.product_template_attribute_value_ids.ids) == wanted:
                return variant
        return self.env["product.product"]

    def _shopify_price_to_odoo_tax_excluded(self, template, raw_price, product=False, store=False):
        """Convert Shopify tax-included price to tax-excluded using Odoo tax engine."""
        price_included = self._to_float(raw_price, default=0.0)
        taxes = template.taxes_id
        if not taxes:
            company = (
                (store and getattr(store, "company_id", False))
                or self.env.company
            )
            taxes = self.env["account.tax"].search(
                [
                    ("company_id", "=", company.id),
                    ("type_tax_use", "=", "sale"),
                    ("active", "=", True),
                ],
                order="sequence asc, id asc",
                limit=1,
            )
        if not taxes:
            print(
                "[shopify-sync][price-debug] template=%s product=%s raw_price=%s included=%s taxes=[] excluded=%s reason=no_taxes"
                % (
                    template.display_name,
                    getattr(product, "display_name", False) or "n/a",
                    raw_price,
                    price_included,
                    price_included,
                )
            )
            return price_included
        currency = template.currency_id or self.env.company.currency_id
        try:
            tax_res = taxes.with_context(force_price_include=True).compute_all(
                price_included,
                currency=currency,
                quantity=1.0,
                product=product or template.product_variant_id,
                partner=False,
            )
            excluded = self._to_float(tax_res.get("total_excluded"), default=price_included)
            _logger.info(
                "Shopify pricing map | template=%s product=%s raw_included=%s tax_excluded=%s taxes=%s",
                template.display_name,
                getattr(product, "display_name", False) or "n/a",
                price_included,
                excluded,
                taxes.mapped("name"),
            )
            print(
                "[shopify-sync][price-debug] template=%s product=%s raw_price=%s included=%s taxes=%s excluded=%s tax_breakdown=%s"
                % (
                    template.display_name,
                    getattr(product, "display_name", False) or "n/a",
                    raw_price,
                    price_included,
                    taxes.mapped("name"),
                    excluded,
                    tax_res.get("taxes"),
                )
            )
            return excluded
        except Exception:
            print(
                "[shopify-sync][price-debug] template=%s product=%s raw_price=%s included=%s excluded=%s reason=compute_failed"
                % (
                    template.display_name,
                    getattr(product, "display_name", False) or "n/a",
                    raw_price,
                    price_included,
                    price_included,
                )
            )
            return price_included

    def _select_price_axis_line(self, template):
        lines = template.attribute_line_ids.filtered(
            lambda line: getattr(line.attribute_id, "create_variant", "always") != "no_variant"
        ).sorted(key=lambda line: (line.sequence, line.id))
        return lines[:1]

    def _log_variant_import_validation(self, template, variant_rows, shopify_product_id=None, store=False):
        shopify_variants = [row for row in (variant_rows or []) if row.get("payload")]
        shopify_skus = {
            (row.get("payload", {}).get("sku") or "").strip()
            for row in shopify_variants
            if (row.get("payload", {}).get("sku") or "").strip()
        }
        odoo_skus = {
            (sku or "").strip()
            for sku in template.product_variant_ids.mapped("default_code")
            if (sku or "").strip()
        }
        missing_skus = sorted(shopify_skus - odoo_skus)

        price_mismatch = []
        for row in shopify_variants:
            payload = row.get("payload") or {}
            variant = row.get("variant")
            if not variant:
                continue
            expected = self._shopify_price_to_odoo_tax_excluded(
                template,
                payload.get("price"),
                product=variant,
                store=store,
            )
            computed = (template.list_price or 0.0) + sum(
                variant.product_template_attribute_value_ids.mapped("price_extra")
            )
            if abs(expected - computed) > 0.0001:
                price_mismatch.append(
                    {
                        "sku": (payload.get("sku") or "").strip() or variant.default_code or "",
                        "shopify": expected,
                        "odoo": computed,
                    }
                )

        _logger.info(
            "Variant validation | shopify_product=%s template=%s shopify=%s odoo=%s missing=%s shopify_skus=%s odoo_skus=%s price_mismatch=%s",
            shopify_product_id,
            template.id,
            len(shopify_variants),
            len(template.product_variant_ids),
            missing_skus,
            sorted(shopify_skus),
            sorted(odoo_skus),
            price_mismatch,
        )
        return {
            "shopify_count": len(shopify_variants),
            "odoo_count": len(template.product_variant_ids),
            "missing_skus": missing_skus,
            "price_mismatch": price_mismatch,
        }

    def _assert_variant_import_integrity(self, validation_data, shopify_product_id=None, template=None):
        validation_data = validation_data or {}
        shopify_count = int(validation_data.get("shopify_count") or 0)
        odoo_count = int(validation_data.get("odoo_count") or 0)
        missing_skus = validation_data.get("missing_skus") or []
        if shopify_count != odoo_count or missing_skus:
            raise ValidationError(
                _(
                    "Variant import integrity check failed for Shopify product %(shopify)s "
                    "(template %(template)s): shopify_count=%(shopify_count)s, "
                    "odoo_count=%(odoo_count)s, missing_skus=%(missing)s"
                )
                % {
                    "shopify": shopify_product_id or "",
                    "template": template.id if template else "",
                    "shopify_count": shopify_count,
                    "odoo_count": odoo_count,
                    "missing": ", ".join(missing_skus) if missing_skus else "[]",
                }
            )

    def _apply_variant_price_model(self, template, variant_rows, store=False):
        """Apply Shopify prices as template base + non-negative PTAV price extras."""
        if not variant_rows:
            return

        priced_rows = []
        for row in variant_rows:
            payload = row.get("payload") or {}
            variant = row.get("variant")
            if not variant:
                continue
            excluded_price = self._shopify_price_to_odoo_tax_excluded(
                template,
                payload.get("price"),
                product=variant,
                store=store,
            )
            priced_rows.append((payload, variant, excluded_price))
        if not priced_rows:
            return

        base_price = min(price for _payload, _variant, price in priced_rows)
        if abs((template.list_price or 0.0) - base_price) > 1e-9:
            template.write({"list_price": base_price})

        if template.attribute_line_ids:
            template.attribute_line_ids.product_template_value_ids.write({"price_extra": 0.0})

        axis_line = self._select_price_axis_line(template)
        axis_line = axis_line[:1]
        if not axis_line:
            return
        axis_line = axis_line[0]

        grouped_targets = {}
        for _payload, variant, excluded_price in priced_rows:
            target_extra = max(0.0, excluded_price - base_price)
            carrier_ptav = variant.product_template_attribute_value_ids.filtered(
                lambda ptav: ptav.attribute_line_id.id == axis_line.id
            )[:1]
            if not carrier_ptav:
                continue
            grouped_targets.setdefault(carrier_ptav.id, []).append(target_extra)

        PTAV = self.env["product.template.attribute.value"]
        for ptav_id, targets in grouped_targets.items():
            if not targets:
                continue
            min_target = min(targets)
            max_target = max(targets)
            if abs(max_target - min_target) > 0.0001:
                _logger.warning(
                    "Variant pricing axis conflict | template=%s ptav=%s min=%s max=%s",
                    template.id,
                    ptav_id,
                    min_target,
                    max_target,
                )
            price_extra = max(0.0, min_target)
            ptav = PTAV.browse(ptav_id)
            if ptav and abs((ptav.price_extra or 0.0) - price_extra) > 1e-9:
                ptav.write({"price_extra": price_extra})

    def _upsert_variant(self, template, variant_payload, ptav_ids=None, store=False):
        ProductProduct = self.env["product.product"]

        sku = variant_payload.get("sku") or False
        barcode = variant_payload.get("barcode") or False
        weight = variant_payload.get("weight")
        shopify_variant_id = variant_payload.get("id")
        print(
            "[shopify-sync][variant-debug] begin template=%s shopify_variant_id=%s sku=%s options=(%s,%s,%s) ptav_ids=%s"
            % (
                template.display_name,
                shopify_variant_id,
                sku or "",
                variant_payload.get("option1") or "",
                variant_payload.get("option2") or "",
                variant_payload.get("option3") or "",
                ptav_ids or [],
            )
        )

        variant = self.env["product.product"]
        if store and shopify_variant_id:
            vmap = self.env["shopify.variant.map"].search(
                [
                    ("store_id", "=", store.id),
                    ("shopify_variant_id", "=", str(shopify_variant_id)),
                ],
                limit=1,
            )
            if vmap and vmap.product_id and vmap.product_id.exists():
                variant = vmap.product_id
                print(
                    "[shopify-sync][variant-debug] matched_by=map shopify_variant_id=%s odoo_variant_id=%s odoo_sku=%s"
                    % (
                        shopify_variant_id,
                        variant.id,
                        variant.default_code or "",
                    )
                )

        if not variant:
            variant = self._find_variant_by_ptav_ids(template, ptav_ids or [])
            if variant:
                print(
                    "[shopify-sync][variant-debug] matched_by=ptav shopify_variant_id=%s odoo_variant_id=%s odoo_sku=%s"
                    % (
                        shopify_variant_id,
                        variant.id,
                        variant.default_code or "",
                    )
                )
        if not variant:
            if sku:
                variant = ProductProduct.search(
                    [("product_tmpl_id", "=", template.id), ("default_code", "=", sku)],
                    limit=1,
                )
                if variant:
                    print(
                        "[shopify-sync][variant-debug] matched_by=sku shopify_variant_id=%s sku=%s odoo_variant_id=%s"
                        % (shopify_variant_id, sku, variant.id)
                    )
        variant_vals = {
            "default_code": sku,
            "barcode": barcode or False,
        }
        if weight is not None:
            try:
                variant_vals["weight"] = float(weight)
            except (TypeError, ValueError):
                pass

        action = "updated"
        if not variant:
            variant_vals["product_tmpl_id"] = template.id
            if ptav_ids:
                variant_vals["product_template_attribute_value_ids"] = [(6, 0, ptav_ids)]
            try:
                variant = ProductProduct.create(variant_vals)
                action = "created"
            except Exception as e:
                if "product_product_combination_unique" not in str(e or ""):
                    raise
                # Concurrency-safe fallback: if same combination was just created, reuse it.
                variant = self._find_variant_by_ptav_ids(template, ptav_ids or [])
                if not variant:
                    raise
                variant.write(variant_vals)
                action = "updated"
            print(
                "[shopify-sync][variant-debug] action=created shopify_variant_id=%s sku=%s odoo_variant_id=%s"
                % (shopify_variant_id, sku or "", variant.id)
            )
        else:
            variant.write(variant_vals)
            print(
                "[shopify-sync][variant-debug] action=updated shopify_variant_id=%s sku=%s odoo_variant_id=%s"
                % (shopify_variant_id, sku or "", variant.id)
            )

        resolved_attrs = self.env["product.template.attribute.value"].browse(ptav_ids or []).mapped("name")
        _logger.info(
            "Variant import -> SKU=%s, attrs=%s, action=%s, option1=%s, option2=%s, option3=%s",
            sku or "",
            resolved_attrs,
            action,
            variant_payload.get("option1") or "",
            variant_payload.get("option2") or "",
            variant_payload.get("option3") or "",
        )

        return variant

    def _upsert_variant_map(self, VariantMap, store, shopify_product_id, variant_payload, variant):
        shopify_variant_id = variant_payload.get("id")
        inventory_item_id = variant_payload.get("inventory_item_id")

        vmap = VariantMap.search(
            [
                ("store_id", "=", store.id),
                ("shopify_variant_id", "=", str(shopify_variant_id)),
            ],
            limit=1,
        )
        vals_map = {
            "store_id": store.id,
            "shopify_product_id": str(shopify_product_id),
            "shopify_variant_id": str(shopify_variant_id),
            "shopify_inventory_item_id": str(inventory_item_id) if inventory_item_id else False,
            "product_id": variant.id,
        }
        if vmap:
            vmap.write(vals_map)
        else:
            VariantMap.create(vals_map)

    def _extract_shopify_product_from_response(self, response):
        """Normalize Shopify product response across possible payload shapes."""
        if not response:
            return {}
        if isinstance(response, dict):
            if isinstance(response.get("product"), dict):
                return response.get("product")
            products = response.get("products")
            if isinstance(products, list) and len(products) == 1 and isinstance(products[0], dict):
                return products[0]
            data = response.get("data")
            if isinstance(data, dict) and isinstance(data.get("product"), dict):
                return data.get("product")
        return response if isinstance(response, dict) else {}

    def _extract_shopify_numeric_id(self, product_payload):
        """Return Shopify numeric product id as string when possible."""
        if not isinstance(product_payload, dict):
            return None
        product_id = product_payload.get("id")
        if product_id not in (None, False, ""):
            return str(product_id)
        gql_id = product_payload.get("admin_graphql_api_id") or product_payload.get("graphql_id")
        if isinstance(gql_id, str) and gql_id:
            # e.g. gid://shopify/Product/123456789
            return gql_id.rsplit("/", 1)[-1]
        return None

    def _cleanup_duplicate_mappings(self, store):
        ProductMap = self.env["shopify.product.map"]
        self.env.cr.execute(
            """
            SELECT shopify_product_id, COUNT(id)
            FROM shopify_product_map
            WHERE store_id = %s
              AND shopify_product_id IS NOT NULL
              AND shopify_product_id <> ''
            GROUP BY shopify_product_id
            HAVING COUNT(id) > 1
            """,
            (store.id,),
        )
        grouped = self.env.cr.fetchall()
        for shopify_product_id, _duplicate_count in grouped:

            mappings = ProductMap.search(
                [
                    ("store_id", "=", store.id),
                    ("shopify_product_id", "=", shopify_product_id),
                ],
                order="id asc",
            )
            keeper = mappings[:1]
            to_delete = mappings[1:]
            if not to_delete:
                continue

            affected_products = to_delete.mapped("product_tmpl_id.display_name")
            _logger.warning(
                "Duplicate Shopify mappings cleaned | store=%s | shopify_id=%s | kept_map_id=%s | deleted_map_ids=%s | affected_products=%s",
                store.display_name,
                shopify_product_id,
                keeper.id,
                to_delete.ids,
                affected_products,
            )
            for mapping in to_delete:
                _logger.warning(
                    json.dumps(
                        {
                            "event": "mapping_recovery",
                            "product": mapping.product_tmpl_id.display_name,
                            "old_shopify_id": shopify_product_id,
                            "action": "deleted_duplicate",
                        }
                    )
                )
            to_delete.unlink()

    def _json_log(self, payload, level="info"):
        logger_method = getattr(_logger, level, _logger.info)
        logger_method(json.dumps(payload, default=str))

    def _log_product_export(
        self,
        product_tmpl,
        action,
        status,
        shopify_id=None,
        variants=0,
        error=None,
        extra=None,
        level="info",
    ):
        payload = {
            "event": "product_export",
            "product": product_tmpl.display_name,
            "action": action,
            "shopify_id": str(shopify_id) if shopify_id else None,
            "variants": int(variants or 0),
            "status": status,
        }
        if error:
            payload["error"] = str(error)
        if extra:
            payload.update(extra)
        self._json_log(payload, level=level)

    def _log_custom_field_sync(
        self,
        store,
        product_tmpl,
        direction,
        status,
        shopify_id=None,
        metafield_count=0,
        reason=None,
        extra=None,
        level="info",
    ):
        payload = {
            "event": "custom_field_sync",
            "direction": direction,
            "status": status,
            "product": product_tmpl.display_name if product_tmpl else None,
            "shopify_id": str(shopify_id) if shopify_id else None,
            "metafield_count": int(metafield_count or 0),
            "reason": reason or None,
        }
        if extra:
            payload.update(extra)
        self._json_log(payload, level=level)

        # Also persist a clean business log entry visible in Odoo UI.
        if store and product_tmpl:
            msg = "Custom fields %s %s" % (
                "import" if direction == "shopify_to_odoo" else "export",
                status,
            )
            if reason:
                msg = "%s (%s)" % (msg, reason)
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="product",
                message=msg,
                payload=payload,
                status="success" if status in ("done", "skipped") else "failed",
                shopify_id=shopify_id,
                error_type=(reason or "").strip() or False,
            )

    def _compute_product_checksum(self, product_data):
        serialized = json.dumps(product_data or {}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _compute_export_idempotency_key(self, product_tmpl, product_data):
        serialized = json.dumps(product_data or {}, sort_keys=True, separators=(",", ":"))
        raw = "%s:%s" % (product_tmpl.id, serialized)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _product_map_has_column(self, column_name):
        self.env.cr.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'shopify_product_map'
              AND column_name = %s
            LIMIT 1
            """,
            (column_name,),
        )
        return bool(self.env.cr.fetchone())

    def _lock_product_export(self, store, product_tmpl):
        self.env.cr.execute(
            """
            SELECT id
            FROM shopify_product_map
            WHERE store_id = %s AND product_tmpl_id = %s
            FOR UPDATE
            """,
            (store.id, product_tmpl.id),
        )
        lock_row = self.env.cr.fetchone()
        if lock_row:
            return lock_row[0]
        lock_seed = "%s:%s" % (store.id, product_tmpl.id)
        advisory_lock_key = int(hashlib.sha256(lock_seed.encode("utf-8")).hexdigest()[:16], 16)
        if advisory_lock_key >= 2**63:
            advisory_lock_key -= 2**64
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", (advisory_lock_key,))
        return None

    def _throttle_on_rate_limit(self, api_client):
        headers = getattr(api_client, "last_response_headers", {}) or {}
        limit_header = headers.get("X-Shopify-Shop-Api-Call-Limit")
        if not limit_header:
            return
        try:
            used, total = [int(v.strip()) for v in str(limit_header).split("/", 1)]
        except Exception:
            return
        if total == 40 and used > 35:
            time.sleep(1)

    def _mark_exported_field_states(self, store, product_tmpl, field_names):
        local_ts = product_tmpl.write_date or fields.Datetime.now()
        for field_name in field_names:
            self._mark_field_sync_state(
                store=store,
                template=product_tmpl,
                field_name=field_name,
                source="odoo",
                local_updated_at=local_ts,
            )
        print(
            "[shopify-sync] marked odoo field states product=%s count=%s"
            % (product_tmpl.display_name, len(field_names))
        )

    def _validate_product_export_response(self, response):
        response_payload = response if isinstance(response, dict) else {}
        product_payload = self._extract_shopify_product_from_response(response_payload)
        if not isinstance(product_payload, dict) or not product_payload:
            self._json_log(
                {
                    "event": "product_export",
                    "status": "failed",
                    "error": "missing_product_payload",
                    "response": response_payload,
                },
                level="error",
            )
            raise ValidationError(_("Shopify API response missing 'product'."))

        if not product_payload.get("id"):
            self._json_log(
                {
                    "event": "product_export",
                    "status": "failed",
                    "error": "missing_product_id",
                    "response": response_payload,
                },
                level="error",
            )
            raise ValidationError(_("Shopify API response missing 'product.id'."))

        if not isinstance(product_payload.get("variants"), list):
            self._json_log(
                {
                    "event": "product_export",
                    "status": "failed",
                    "error": "missing_variants_list",
                    "response": response_payload,
                },
                level="error",
            )
            raise ValidationError(_("Shopify API response missing 'product.variants' list."))

        return product_payload

    def _ensure_product_map_runtime_columns(self):
        """Hotfix for environments where module upgrade has not run yet."""
        statements = [
            "ALTER TABLE shopify_product_map ADD COLUMN IF NOT EXISTS checksum varchar",
            "ALTER TABLE shopify_product_map ADD COLUMN IF NOT EXISTS last_sync_at timestamp",
            "ALTER TABLE shopify_product_map ADD COLUMN IF NOT EXISTS idempotency_key varchar",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS shopify_product_map_store_idem_uniq_idx "
                "ON shopify_product_map (store_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            ),
        ]
        for sql in statements:
            try:
                self.env.cr.execute(sql)
            except Exception as exc:
                self._json_log(
                    {
                        "event": "product_export",
                        "status": "failed",
                        "error": "runtime_schema_patch_failed",
                        "sql": sql,
                        "details": str(exc),
                    },
                    level="warning",
                )

    def _get_valid_shopify_product_id(self, product_tmpl, store, api_client):
        ProductMap = self.env["shopify.product.map"]
        mapping = ProductMap.search(
            [
                ("store_id", "=", store.id),
                ("product_tmpl_id", "=", product_tmpl.id),
            ],
            limit=1,
        )
        if not mapping or not mapping.shopify_product_id:
            return False

        shopify_id = mapping.shopify_product_id
        other_mapping = ProductMap.search(
            [
                ("store_id", "=", store.id),
                ("shopify_product_id", "=", shopify_id),
                ("product_tmpl_id", "!=", product_tmpl.id),
            ],
            limit=1,
        )
        if other_mapping:
            _logger.critical(
                "Ownership conflict for Shopify product %s | product=%s | other_product=%s. Forcing create.",
                shopify_id,
                product_tmpl.display_name,
                other_mapping.product_tmpl_id.display_name,
            )
            _logger.warning(
                json.dumps(
                    {
                        "event": "mapping_recovery",
                        "product": product_tmpl.display_name,
                        "old_shopify_id": shopify_id,
                        "action": "forced_create",
                    }
                )
            )
            return False
        try:
            api_client.get_product_by_id(shopify_id)
            return shopify_id
        except Exception:
            _logger.warning(
                "Invalid Shopify mapping detected. Product '%s' has broken ID %s",
                product_tmpl.name,
                shopify_id,
            )
            _logger.warning(
                json.dumps(
                    {
                        "event": "mapping_recovery",
                        "product": product_tmpl.display_name,
                        "old_shopify_id": shopify_id,
                        "action": "deleted_duplicate",
                    }
                )
            )
            mapping.unlink()
            return False

    def _find_shopify_product_by_sku(self, api_client, sku):
        if not sku:
            return False
        products = api_client.get_products(limit=250) or []
        matched_product_ids = []
        for product in products:
            for variant in product.get("variants", []):
                if variant.get("sku") == sku:
                    product_id = self._extract_shopify_numeric_id(product)
                    if product_id:
                        matched_product_ids.append(str(product_id))
                    break
        matched_product_ids = sorted(set(matched_product_ids))
        if not matched_product_ids:
            return False
        if len(matched_product_ids) > 1:
            raise ValidationError(
                _(
                    "SKU '%s' matched multiple Shopify products (%s). Resolve duplicates in Shopify before export."
                )
                % (sku, ", ".join(matched_product_ids))
            )
        return matched_product_ids[0]

    def _recover_shopify_id_from_catalog(self, api_client, payload, existing_id=None):
        """Fallback resolver when export response does not include a product id."""
        if existing_id:
            return str(existing_id)

        variants = payload.get("variants") or []
        target_skus = {
            (v.get("sku") or "").strip()
            for v in variants
            if isinstance(v, dict) and (v.get("sku") or "").strip()
        }
        target_title = (payload.get("title") or "").strip()

        products = api_client.get_products(limit=250) or []
        for product in products:
            if not isinstance(product, dict):
                continue
            product_title = (product.get("title") or "").strip()
            product_variants = product.get("variants") or []
            product_skus = {
                (pv.get("sku") or "").strip()
                for pv in product_variants
                if isinstance(pv, dict) and (pv.get("sku") or "").strip()
            }
            if target_skus and product_skus.intersection(target_skus):
                recovered_id = self._extract_shopify_numeric_id(product)
                if recovered_id:
                    return recovered_id
            if target_title and product_title == target_title and product_skus.intersection(target_skus):
                recovered_id = self._extract_shopify_numeric_id(product)
                if recovered_id:
                    return recovered_id

        return None

    def _resolve_safe_sku_recovery(self, product_tmpl, store, recovered_id):
        if not recovered_id:
            return False
        ProductMap = self.env["shopify.product.map"]
        owner = ProductMap.search(
            [
                ("store_id", "=", store.id),
                ("shopify_product_id", "=", str(recovered_id)),
            ],
            limit=1,
        )
        if owner and owner.product_tmpl_id.id != product_tmpl.id:
            self._log_product_export(
                product_tmpl=product_tmpl,
                action="create",
                status="failed",
                shopify_id=recovered_id,
                variants=len(product_tmpl.product_variant_ids),
                error="sku_recovery_conflict",
                extra={"mapped_to_product": owner.product_tmpl_id.display_name},
                level="error",
            )
            return False
        return str(recovered_id)

    def _resolve_create_conflict_shopify_id(self, api_client, product_tmpl, store):
        sku = (product_tmpl.product_variant_ids[:1].default_code or "").strip()
        if not sku:
            return False
        recovered_id = self._find_shopify_product_by_sku(api_client, sku)
        return self._resolve_safe_sku_recovery(
            product_tmpl=product_tmpl,
            store=store,
            recovered_id=recovered_id,
        )

    def import_shopify_product(self, payload, store):
        """Create/update product template, variants, attributes, category, images, tags, and mapping."""
        ProductTemplate = self.env["product.template"]
        ProductProduct = self.env["product.product"]
        ProductAttribute = self.env["product.attribute"]
        ProductAttributeValue = self.env["product.attribute.value"]
        ProductMap = self.env["shopify.product.map"]
        VariantMap = self.env["shopify.variant.map"]

        shopify_product_id = payload.get("id")
        title = payload.get("title") or ""
        product_type = payload.get("product_type") or ""
        tags_string = payload.get("tags") or ""
        variants = payload.get("variants") or []
        options = payload.get("options") or []
        images = self._extract_images(payload)

        product_map = ProductMap.search(
            [
                ("store_id", "=", store.id),
                ("shopify_product_id", "=", str(shopify_product_id)),
            ],
            limit=1,
        )

        template = self._find_or_create_template(title, product_map)
        if not template:
            first_sku = ""
            if variants and isinstance(variants[0], dict):
                first_sku = variants[0].get("sku") or ""
            template = self._find_template_by_sku(first_sku)
            if template:
                _logger.info(
                    "Shopify product import matched existing template by SKU | product_id=%s sku=%s template_id=%s",
                    shopify_product_id,
                    first_sku,
                    template.id,
                )
        shopify_updated = self._parse_shopify_datetime(payload.get("updated_at"))
        print(
            "[shopify-sync] import product id=%s updated_at=%s"
            % (shopify_product_id, payload.get("updated_at"))
        )
        print(
            "[shopify-sync][variant-debug] payload_variant_count=%s payload_skus=%s"
            % (
                len(variants),
                [
                    (v.get("sku") or "").strip()
                    for v in variants
                    if isinstance(v, dict)
                ],
            )
        )
        print("=====================here is all about variants=====================")
        print(
            "[shopify-sync][variant-debug] block_start product_id=%s template=%s variants=%s"
            % (shopify_product_id, title or "n/a", len(variants))
        )

        # Category from product_type
        categ = self._get_or_create_category(product_type)
        # Tags from Shopify tags
        tag_ids = self._get_or_create_tags(tags_string)

        template_vals = self._build_template_vals(payload, store, categ, tag_ids)
        if template:
            filtered_vals = {}
            for field_name, value in template_vals.items():
                if self._should_apply_remote_field(
                    store=store,
                    template=template,
                    field_name=field_name,
                    remote_updated_at=shopify_updated,
                ):
                    filtered_vals[field_name] = value
                    self._mark_field_sync_state(
                        store=store,
                        template=template,
                        field_name=field_name,
                        source="shopify",
                        remote_updated_at=shopify_updated,
                    )
            template_vals = filtered_vals
        template = self._upsert_template(template, template_vals)

        image_import_state, image_import_message = self._sync_template_main_image(
            template, store, shopify_product_id, images, variants
        )

        self._ensure_product_map(ProductMap, store, shopify_product_id, template, product_map)

        attribute_map, value_map = self._build_attribute_value_maps(options, variants=variants)
        self._apply_template_attribute_lines(template, attribute_map, value_map)

        variant_rows = []
        for variant_payload in variants:
            ptav_ids = self._resolve_ptav_ids(template, variant_payload, attribute_map, value_map)
            variant = self._upsert_variant(
                template, variant_payload, ptav_ids=ptav_ids, store=store
            )
            if ptav_ids:
                variant.write(
                    {"product_template_attribute_value_ids": [(6, 0, ptav_ids)]}
                )
            self._upsert_variant_map(
                VariantMap, store, shopify_product_id, variant_payload, variant
            )
            print(
                "[shopify-sync][variant-debug] mapped shopify_variant_id=%s -> odoo_variant_id=%s sku=%s"
                % (
                    variant_payload.get("id"),
                    variant.id,
                    variant.default_code or "",
                )
            )
            variant_rows.append({"payload": variant_payload, "variant": variant})

        self._apply_variant_price_model(template, variant_rows, store=store)
        validation_data = self._log_variant_import_validation(
            template, variant_rows, shopify_product_id=shopify_product_id, store=store
        )
        self._assert_variant_import_integrity(
            validation_data,
            shopify_product_id=shopify_product_id,
            template=template,
        )
        print(
            "[shopify-sync][variant-debug] final_odoo_variant_count=%s final_odoo_skus=%s"
            % (
                len(template.product_variant_ids),
                [sku for sku in template.product_variant_ids.mapped("default_code") if sku],
            )
        )
        print(
            "[shopify-sync][variant-debug] block_end product_id=%s template=%s"
            % (shopify_product_id, template.display_name)
        )
        print("======================finish variants log======================")

        # Import custom addon fields from Shopify metafields when available.
        try:
            metafields = store._get_api_client().get_product_metafields(shopify_product_id)
            if not license_is_active_strict(self.env):
                metafields = (metafields or [])[: trial_batch_limit() + 3]
                time.sleep(1)
            self._apply_custom_metafields_to_template(template, store, metafields)
            self._log_custom_field_sync(
                store=store,
                product_tmpl=template,
                direction="shopify_to_odoo",
                status="done",
                shopify_id=shopify_product_id,
                metafield_count=len(metafields or []),
                reason="metafields_applied" if metafields else "no_metafields_found",
            )
        except Exception as exc:
            self._log_custom_field_sync(
                store=store,
                product_tmpl=template,
                direction="shopify_to_odoo",
                status="failed",
                shopify_id=shopify_product_id,
                reason="metafield_import_failed",
                extra={"error": str(exc)},
                level="warning",
            )
            _logger.warning(
                "Failed to sync Shopify custom metafields for product %s: %s",
                shopify_product_id,
                str(exc),
            )

        mapping = ProductMap.search(
            [("store_id", "=", store.id), ("product_tmpl_id", "=", template.id)],
            limit=1,
        )
        if mapping:
            mapping.last_sync_at = fields.Datetime.now()

        return {
            "product_template_id": template.id,
            "image_import_state": image_import_state,
            "image_import_message": image_import_message,
        }

    def export_product_to_shopify(
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
        """Export one Odoo product template to Shopify with enterprise-safe guarantees."""
        api_client = store._get_api_client()
        product_tmpl.ensure_one()
        store.ensure_one()
        mapper = ProductMapper(self.env)

        if product_tmpl.type == "service":
            raise ValidationError(
                _("Product '%s' is a Service and cannot be exported to Shopify.")
                % product_tmpl.name
            )

        product_data = mapper.build_export_product_data(
            product_tmpl=product_tmpl,
            store=store,
            layer=layer,
            export_name=export_name,
            export_description=export_description,
            export_tags=export_tags,
            export_categories=export_categories,
            export_price=export_price,
            export_image=export_image,
            publish_option=publish_option,
        )
        product_data = mapper.validate_export_product_data(
            product_data,
            product_name=product_tmpl.display_name or product_tmpl.name,
        )
        self._ensure_product_map_runtime_columns()
        ProductMap = self.env["shopify.product.map"]
        VariantMap = self.env["shopify.variant.map"]
        checksum = self._compute_product_checksum(product_data)
        idempotency_key = self._compute_export_idempotency_key(product_tmpl, product_data)
        has_checksum = self._product_map_has_column("checksum")
        has_last_sync_at = self._product_map_has_column("last_sync_at")
        has_idempotency_key = self._product_map_has_column("idempotency_key")

        with self.env.cr.savepoint():
            self._cleanup_duplicate_mappings(store)
            self._lock_product_export(store, product_tmpl)

            if has_idempotency_key:
                duplicate_job = ProductMap.search(
                    [
                        ("store_id", "=", store.id),
                        ("idempotency_key", "=", idempotency_key),
                        ("product_tmpl_id", "!=", product_tmpl.id),
                    ],
                    limit=1,
                )
                if duplicate_job:
                    self._log_product_export(
                        product_tmpl=product_tmpl,
                        action="update",
                        status="skipped",
                        shopify_id=duplicate_job.shopify_product_id,
                        variants=len(product_data.get("variants") or []),
                        error="duplicate_idempotency_key",
                        level="warning",
                    )
                    return {
                        "shopify_product_id": duplicate_job.shopify_product_id,
                        "created": False,
                        "skipped": True,
                        "reason": "duplicate_idempotency_key",
                    }

            product_map = ProductMap.search(
                [
                    ("store_id", "=", store.id),
                    ("product_tmpl_id", "=", product_tmpl.id),
                ],
                limit=1,
            )
            if has_checksum and product_map and product_map.checksum and product_map.checksum == checksum:
                self._log_product_export(
                    product_tmpl=product_tmpl,
                    action="update" if product_map.shopify_product_id else "create",
                    status="skipped",
                    shopify_id=product_map.shopify_product_id,
                    variants=len(product_data.get("variants") or []),
                    extra={"reason": "skipped_no_change"},
                )
                return {
                    "shopify_product_id": product_map.shopify_product_id,
                    "created": False,
                    "skipped": True,
                    "reason": "skipped_no_change",
                }

            existing_id = self._get_valid_shopify_product_id(product_tmpl, store, api_client)
            if not existing_id:
                sku = product_tmpl.product_variant_ids[:1].default_code
                recovered_id = self._find_shopify_product_by_sku(api_client, sku)
                existing_id = self._resolve_safe_sku_recovery(
                    product_tmpl=product_tmpl,
                    store=store,
                    recovered_id=recovered_id,
                )

            action = "update" if existing_id else "create"
            if existing_id:
                try:
                    shopify_product = api_client.get_product_by_id(existing_id) or {}
                    shopify_updated = self._parse_shopify_datetime(
                        shopify_product.get("updated_at")
                    )
                    odoo_updated = product_tmpl.write_date
                    if shopify_updated and odoo_updated:
                        print(
                            "[shopify-sync] export conflict check product=%s shopify_updated=%s odoo_updated=%s"
                            % (product_tmpl.display_name, shopify_updated, odoo_updated)
                        )
                except Exception:
                    # If timestamp fetch fails, keep export behavior unchanged.
                    pass
            self._log_product_export(
                product_tmpl=product_tmpl,
                action=action,
                status="processing",
                shopify_id=existing_id,
                variants=len(product_data.get("variants") or []),
            )
            _logger.info(
                "Export variants count=%s SKUs=%s",
                len(product_data.get("variants") or []),
                [
                    variant.get("sku")
                    for variant in (product_data.get("variants") or [])
                ],
            )
            _logger.info(
                "Shopify export payload variants=%s",
                product_data.get("variants") or [],
            )
            _logger.warning(
                "VARIANT PAYLOAD DEBUG => %s",
                product_data.get("variants") or [],
            )

            if existing_id:
                variants_without_id = [
                    variant.get("sku")
                    for variant in (product_data.get("variants") or [])
                    if isinstance(variant, dict) and not variant.get("id")
                ]
                if variants_without_id:
                    _logger.warning(
                        "Update payload missing variant id for SKUs=%s product=%s shopify_id=%s",
                        variants_without_id,
                        product_tmpl.display_name,
                        existing_id,
                    )
                    try:
                        remote_product = api_client.get_product_by_id(existing_id) or {}
                        remote_by_sku = {
                            (remote_variant.get("sku") or "").strip(): remote_variant
                            for remote_variant in (remote_product.get("variants") or [])
                            if (remote_variant.get("sku") or "").strip()
                        }
                        for variant in (product_data.get("variants") or []):
                            if not isinstance(variant, dict) or variant.get("id"):
                                continue
                            sku = (variant.get("sku") or "").strip()
                            remote_variant = remote_by_sku.get(sku)
                            remote_id = remote_variant.get("id") if remote_variant else False
                            if remote_id:
                                try:
                                    variant["id"] = int(remote_id)
                                except (TypeError, ValueError):
                                    variant["id"] = remote_id
                        still_missing_ids = [
                            variant.get("sku")
                            for variant in (product_data.get("variants") or [])
                            if isinstance(variant, dict) and not variant.get("id")
                        ]
                        if still_missing_ids:
                            _logger.warning(
                                "Unable to hydrate variant ids from Shopify for SKUs=%s product=%s shopify_id=%s",
                                still_missing_ids,
                                product_tmpl.display_name,
                                existing_id,
                            )
                        else:
                            _logger.warning(
                                "Hydrated missing variant ids from Shopify by SKU for product=%s shopify_id=%s",
                                product_tmpl.display_name,
                                existing_id,
                            )
                    except Exception as hydration_exc:
                        _logger.warning(
                            "Variant id hydration failed before update for product=%s shopify_id=%s error=%s",
                            product_tmpl.display_name,
                            existing_id,
                            hydration_exc,
                        )
                try:
                    response = api_client.update_product(existing_id, product_data, location_id=store.shopify_location_id)
                    created = False
                except Exception as exc:
                    # If mapped update payload is rejected, fall back to create to avoid hard stop.
                    exc_str = str(exc)
                    if "Required parameter missing or invalid" in exc_str:
                        self._log_product_export(
                            product_tmpl=product_tmpl,
                            action="update",
                            status="failed",
                            shopify_id=existing_id,
                            variants=len(product_data.get("variants") or []),
                            error="update_payload_invalid_fallback_to_create",
                            level="warning",
                        )
                        response = api_client.create_product(product_data, location_id=store.shopify_location_id)
                        created = True
                        action = "create"
                    # If product was deleted on Shopify, recreate it
                    elif "404" in exc_str or "Not Found" in exc_str or "Could not find product" in exc_str:
                        self._log_product_export(
                            product_tmpl=product_tmpl,
                            action="update",
                            status="failed",
                            shopify_id=existing_id,
                            variants=len(product_data.get("variants") or []),
                            error="product_not_found_recreating",
                            level="warning",
                        )
                        product_map = self.env["shopify.product.map"].search([
                            ("product_tmpl_id", "=", product_tmpl.id),
                            ("store_id", "=", store.id),
                        ], limit=1)
                        if product_map:
                            product_map.sudo().write({
                                'shopify_product_id': False,
                                'checksum': False,
                                'last_exported_at': False,
                            })
                        response = api_client.create_product(product_data, location_id=store.shopify_location_id)
                        created = True
                        action = "create"
                    else:
                        raise
            else:
                response = api_client.create_product(product_data, location_id=store.shopify_location_id)
                created = True
            self._throttle_on_rate_limit(api_client)

            # Diagnostic logging for API response debugging
            self._json_log({
                "event": "product_export_api_response",
                "product": product_tmpl.display_name,
                "action": action,
                "response_preview": str(response)[:500] if response else None,
                "response_keys": list(response.keys()) if isinstance(response, dict) else None,
            }, level="debug")

            res_product = self._validate_product_export_response(response)
            shopify_id = str(res_product.get("id"))

            existing = ProductMap.search(
                [
                    ("store_id", "=", store.id),
                    ("shopify_product_id", "=", shopify_id),
                    ("product_tmpl_id", "!=", product_tmpl.id),
                ],
                limit=1,
            )
            if existing and action == "create":
                recovered_shopify_id = self._resolve_create_conflict_shopify_id(
                    api_client=api_client,
                    product_tmpl=product_tmpl,
                    store=store,
                )
                if recovered_shopify_id:
                    shopify_id = recovered_shopify_id
                    existing = ProductMap.search(
                        [
                            ("store_id", "=", store.id),
                            ("shopify_product_id", "=", shopify_id),
                            ("product_tmpl_id", "!=", product_tmpl.id),
                        ],
                        limit=1,
                    )
            if existing:
                raise ValidationError(
                    _(
                        "CRITICAL: Shopify product %s already mapped to another product!"
                    )
                    % shopify_id
                )

            vals = {
                "store_id": store.id,
                "shopify_product_id": shopify_id,
                "product_tmpl_id": product_tmpl.id,
            }
            if has_checksum:
                vals["checksum"] = checksum
            if has_last_sync_at:
                vals["last_sync_at"] = fields.Datetime.now()
            if has_idempotency_key:
                vals["idempotency_key"] = idempotency_key
            if not product_map:
                product_map = ProductMap.create(vals)
            else:
                product_map.write(vals)

            # Export custom addon fields as Shopify metafields.
            metafield_sync_success = True
            metafield_sync_error = None
            try:
                custom_metafields = mapper.build_export_metafields(product_tmpl)
                if not license_is_active_strict(self.env):
                    custom_metafields = (custom_metafields or [])[: trial_batch_limit() + 1]
                    time.sleep(1)
                print(
                    "[shopify-sync] exporting custom metafields product=%s count=%s"
                    % (product_tmpl.display_name, len(custom_metafields or []))
                )
                api_client.upsert_product_metafields(shopify_id, custom_metafields)
                self._log_custom_field_sync(
                    store=store,
                    product_tmpl=product_tmpl,
                    direction="odoo_to_shopify",
                    status="done",
                    shopify_id=shopify_id,
                    metafield_count=len(custom_metafields or []),
                    reason="metafields_upserted",
                )
            except Exception as exc:
                metafield_sync_success = False
                metafield_sync_error = str(exc)
                _logger.exception("Metafield sync failed")
                self._log_custom_field_sync(
                    store=store,
                    product_tmpl=product_tmpl,
                    direction="odoo_to_shopify",
                    status="failed",
                    shopify_id=shopify_id,
                    reason="metafield_export_failed",
                    extra={"error": str(exc)},
                    level="warning",
                )
                self._log_product_export(
                    product_tmpl=product_tmpl,
                    action=action,
                    status="failed",
                    shopify_id=shopify_id,
                    variants=len(res_product.get("variants") or []),
                    error="metafield_export_failed: %s" % str(exc),
                    level="warning",
                )

            self._mark_exported_field_states(
                store=store,
                product_tmpl=product_tmpl,
                field_names=[
                    "name",
                    "description",
                    "description_sale",
                    "list_price",
                    "categ_id",
                    "brand_id",
                    "type_id",
                    "collection_ids",
                    "hair_type_ids",
                    "benefits",
                    "description_long",
                    "application",
                    "ingredients",
                    "inci_list",
                    "meta_title",
                    "meta_description",
                    "block1_title",
                    "block1_text",
                    "block1_image",
                    "block2_title",
                    "block2_text",
                    "block2_image",
                    "cross_sell_ids",
                    "similar_product_ids",
                ],
            )

            variant_by_sku = {}
            for variant_record in product_tmpl.product_variant_ids:
                sku = (variant_record.default_code or "").strip()
                if sku:
                    variant_by_sku[sku] = variant_record

            for shopify_variant in res_product.get("variants") or []:
                shopify_variant_id = str(shopify_variant.get("id") or "")
                if not shopify_variant_id:
                    continue
                sku = (shopify_variant.get("sku") or "").strip()
                if not sku:
                    self._log_product_export(
                        product_tmpl=product_tmpl,
                        action=action,
                        status="failed",
                        shopify_id=shopify_id,
                        variants=len(res_product.get("variants") or []),
                        error="variant_sku_missing",
                        extra={"shopify_variant_id": shopify_variant_id},
                        level="warning",
                    )
                    continue
                product_product = variant_by_sku.get(sku)
                if not product_product:
                    self._log_product_export(
                        product_tmpl=product_tmpl,
                        action=action,
                        status="failed",
                        shopify_id=shopify_id,
                        variants=len(res_product.get("variants") or []),
                        error="variant_sku_not_found",
                        extra={"sku": sku, "shopify_variant_id": shopify_variant_id},
                        level="warning",
                    )
                    continue

                inv_item_id = shopify_variant.get("inventory_item_id")
                vmap = VariantMap.search(
                    [("store_id", "=", store.id), ("shopify_variant_id", "=", shopify_variant_id)],
                    limit=1,
                )
                variant_vals = {
                    "store_id": store.id,
                    "shopify_product_id": shopify_id,
                    "shopify_variant_id": shopify_variant_id,
                    "shopify_inventory_item_id": str(inv_item_id) if inv_item_id else False,
                    "product_id": product_product.id,
                }
                if vmap:
                    vmap.write(variant_vals)
                else:
                    VariantMap.create(variant_vals)

            if metafield_sync_success:
                self._log_product_export(
                    product_tmpl=product_tmpl,
                    action=action,
                    status="success",
                    shopify_id=shopify_id,
                    variants=len(res_product.get("variants") or []),
                )
            else:
                self._log_product_export(
                    product_tmpl=product_tmpl,
                    action=action,
                    status="failed",
                    shopify_id=shopify_id,
                    variants=len(res_product.get("variants") or []),
                    error="metafield_export_failed: %s" % (metafield_sync_error or "unknown"),
                    level="warning",
                )
            return {"shopify_product_id": shopify_id, "created": created}
