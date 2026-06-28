import json
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)

class ProductTemplate(models.Model):
    _inherit = "product.template"

    shopify_product_id = fields.Char(
        string="Shopify Product ID",
        help=(
            "Primary Shopify product id for this template (for the main Shopify store). "
            "For multi-store setups, detailed mappings are stored in Shopify product "
            "mapping models and on Shopify product layers."
        ),
    )
    shopify_product_map_ids = fields.One2many(
        "shopify.product.map",
        "product_tmpl_id",
        string="Shopify Product Mappings",
        help="Store-specific Shopify product mappings linked to this Odoo template.",
    )

    def _prepare_shopify_metafields(self):
        self.ensure_one()

        _logger.debug("Meta sync export start for template=%s", self.display_name)

        def _resolve_value_and_type(field_name, value):
            mf_type = "single_line_text_field"

            if value in (None, False):
                return None, mf_type

            # PROPERTY (must be early)
            if value.__class__.__name__ == "Property":
                try:
                    extracted = getattr(value, "value", None)
                    return str(extracted) if extracted else None, mf_type
                except Exception:
                    return str(value), mf_type

            # BOOLEAN → string
            if isinstance(value, bool):
                return "true" if value else "false", mf_type

            # NUMBER → string
            if isinstance(value, (int, float)):
                return str(value), mf_type

            # MANY2ONE
            if hasattr(value, "id") and hasattr(value, "display_name"):
                return str(value.id), mf_type

            # MANY2MANY / ONE2MANY
            if hasattr(value, "ids"):
                try:
                    return json.dumps([str(i) for i in value.ids]), "json"
                except Exception:
                    return None, mf_type

            # BYTES / IMAGE → SKIP
            if isinstance(value, (bytes, bytearray)):
                _logger.warning(
                    "[META WARNING] Skipping binary field %s (bytes not allowed)",
                    field_name,
                )
                return None, mf_type

            # DICT / LIST → JSON
            if isinstance(value, (dict, list)):
                try:
                    return json.dumps(value), "json"
                except Exception:
                    return str(value), mf_type

            # STRING
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return None, mf_type
                return value, mf_type

            # FALLBACK
            try:
                return str(value), mf_type
            except Exception:
                return None, mf_type

        metafields = []

        def _add(metafields, key, value, mtype):
            if value in (None, False):
                return
            metafields.append({
                "namespace": "custom",
                "key": key,
                "value": value,
                "type": mtype,
            })

        # --------------------------------------------------
        # MAIN LOOP (single pipeline for all fields)
        # --------------------------------------------------

        for field_name in self._fields:
            key = field_name

            # Skip technical/internal fields if needed
            if field_name.startswith("_"):
                continue

            # Check field existence (safety)
            if not hasattr(self, field_name):
                _logger.warning("[META WARNING] Missing field on model: %s", field_name)
                _logger.debug("[META DEBUG] Field=%s Raw Value=%s", field_name, "MISSING")
                continue

            raw_val = self[field_name]
            # Skip binary fields (images, attachments, etc.)
            if isinstance(raw_val, (bytes, bytearray)):
                _logger.warning(
                    "[META WARNING] Skipping binary field %s (not allowed in Shopify metafields)",
                    field_name,
                )
                _logger.debug("[META DEBUG] Field=%s Raw Value=BINARY_DATA", field_name)
                continue
            # Skip Odoo recordsets (one2many/many2many/many2one values) not JSON-safe for Shopify
            if isinstance(raw_val, models.BaseModel):
                _logger.warning(
                    "[META WARNING] Skipping recordset field %s (not JSON-serializable for Shopify metafields)",
                    field_name,
                )
                _logger.debug("[META DEBUG] Field=%s Raw Value=RECORDSET_DATA", field_name)
                continue

            _logger.debug(
                "Field %s source=%s raw=%s",
                key,
                field_name,
                str(raw_val)[:100],
            )

            value, mf_type = _resolve_value_and_type(field_name, raw_val)

            # Skip only None / False
            if value in (None, False):
                _logger.info(
                    "[META DEBUG] Field=%s Raw Value=%s",
                    field_name,
                    str(raw_val)[:100],
                )
                continue

            # File reference guard
            if isinstance(value, str) and value.startswith("gid://"):
                mf_type = "file_reference"
            elif isinstance(value, str) and "gid://" in str(raw_val):
                # invalid gid format
                _logger.info(
                    "[META DEBUG] Field=%s Raw Value=%s",
                    field_name,
                    str(raw_val)[:100],
                )
                continue
            # Shopify metafield value hard limit: 65536 chars
            if isinstance(value, str) and len(value) > 65536:
                _logger.warning(
                    "[META WARNING] Skipping oversized field %s (length=%s exceeds Shopify limit)",
                    field_name,
                    len(value),
                )
                _logger.info(
                    "[META DEBUG] Field=%s Raw Value=OVERSIZED_TEXT(%s)",
                    field_name,
                    len(value),
                )
                continue

            _add(metafields, key, value, mf_type)

        # --------------------------------------------------

        _logger.debug(
            "Prepared metafield keys: %s",
            [m.get("key") for m in metafields],
        )
        _logger.debug("Meta sync export finished for template=%s", self.display_name)
        return metafields

