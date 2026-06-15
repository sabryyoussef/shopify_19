import json
import logging

from odoo import fields, models

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

        _logger.info("++++++++=========== META SYNC DEBUG START ===========++++++++")

        def _resolve_value_and_type(field_name, value):
            mf_type = "single_line_text_field"
            # region agent log
            _agent_log(
                {
                    "runId": "pre-fix",
                    "hypothesisId": "EXP_ENTRY",
                    "location": "models/product_template.py:_resolve_value_and_type:entry",
                    "message": "export_resolve_entry",
                    "data": {
                        "field": field_name,
                        "py_type": type(value).__name__,
                        "py_module": getattr(type(value), "__module__", ""),
                        "is_bool": isinstance(value, bool),
                        "is_num": isinstance(value, (int, float)) and not isinstance(value, bool),
                        "is_str": isinstance(value, str),
                        "is_bytes": isinstance(value, (bytes, bytearray)),
                        "has_id": hasattr(value, "id"),
                        "has_display_name": hasattr(value, "display_name"),
                        "has_ids": hasattr(value, "ids"),
                        "class_name": getattr(getattr(value, "__class__", None), "__name__", ""),
                    },
                }
            )
            # endregion agent log

            if value in (None, False):
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_NONE_FALSE",
                        "location": "models/product_template.py:_resolve_value_and_type:none_false",
                        "message": "export_resolve_branch",
                        "data": {"field": field_name, "branch": "none_false"},
                    }
                )
                # endregion agent log
                return None, mf_type

            # PROPERTY (must be early)
            if value.__class__.__name__ == "Property":
                try:
                    extracted = getattr(value, "value", None)
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "EXP_PROPERTY",
                            "location": "models/product_template.py:_resolve_value_and_type:property",
                            "message": "export_resolve_branch",
                            "data": {
                                "field": field_name,
                                "branch": "property",
                                "extracted_type": type(extracted).__name__,
                                "extracted_preview": str(extracted)[:120] if extracted is not None else None,
                            },
                        }
                    )
                    # endregion agent log
                    return str(extracted) if extracted else None, mf_type
                except Exception:
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "EXP_PROPERTY",
                            "location": "models/product_template.py:_resolve_value_and_type:property_exc",
                            "message": "export_resolve_exception",
                            "data": {"field": field_name, "branch": "property_exc"},
                        }
                    )
                    # endregion agent log
                    return str(value), mf_type

            # BOOLEAN → string
            if isinstance(value, bool):
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_BOOL",
                        "location": "models/product_template.py:_resolve_value_and_type:bool",
                        "message": "export_resolve_branch",
                        "data": {"field": field_name, "branch": "bool"},
                    }
                )
                # endregion agent log
                return "true" if value else "false", mf_type

            # NUMBER → string
            if isinstance(value, (int, float)):
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_NUM",
                        "location": "models/product_template.py:_resolve_value_and_type:number",
                        "message": "export_resolve_branch",
                        "data": {"field": field_name, "branch": "number"},
                    }
                )
                # endregion agent log
                return str(value), mf_type

            # MANY2ONE
            if hasattr(value, "id") and hasattr(value, "display_name"):
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_M2O",
                        "location": "models/product_template.py:_resolve_value_and_type:many2one",
                        "message": "export_resolve_branch",
                        "data": {
                            "field": field_name,
                            "branch": "many2one",
                            "id": getattr(value, "id", None),
                        },
                    }
                )
                # endregion agent log
                return str(value.id), mf_type

            # MANY2MANY / ONE2MANY
            if hasattr(value, "ids"):
                try:
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "EXP_RECORDSET_IDS",
                            "location": "models/product_template.py:_resolve_value_and_type:ids",
                            "message": "export_resolve_branch",
                            "data": {
                                "field": field_name,
                                "branch": "ids",
                                "ids_len": len(getattr(value, "ids", []) or []),
                            },
                        }
                    )
                    # endregion agent log
                    return json.dumps([str(i) for i in value.ids]), "json"
                except Exception:
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "EXP_RECORDSET_IDS",
                            "location": "models/product_template.py:_resolve_value_and_type:ids_exc",
                            "message": "export_resolve_exception",
                            "data": {"field": field_name, "branch": "ids_exc"},
                        }
                    )
                    # endregion agent log
                    return None, mf_type

            # BYTES / IMAGE → SKIP
            if isinstance(value, (bytes, bytearray)):
                _logger.warning(
                    "[META WARNING] Skipping binary field %s (bytes not allowed)",
                    field_name,
                )
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_BYTES",
                        "location": "models/product_template.py:_resolve_value_and_type:bytes",
                        "message": "export_resolve_branch",
                        "data": {"field": field_name, "branch": "bytes"},
                    }
                )
                # endregion agent log
                return None, mf_type

            # DICT / LIST → JSON
            if isinstance(value, (dict, list)):
                try:
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "EXP_JSONABLE",
                            "location": "models/product_template.py:_resolve_value_and_type:dict_list",
                            "message": "export_resolve_branch",
                            "data": {"field": field_name, "branch": "dict_list"},
                        }
                    )
                    # endregion agent log
                    return json.dumps(value), "json"
                except Exception:
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "EXP_JSONABLE",
                            "location": "models/product_template.py:_resolve_value_and_type:dict_list_exc",
                            "message": "export_resolve_exception",
                            "data": {"field": field_name, "branch": "dict_list_exc"},
                        }
                    )
                    # endregion agent log
                    return str(value), mf_type

            # STRING
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    # region agent log
                    _agent_log(
                        {
                            "runId": "pre-fix",
                            "hypothesisId": "EXP_STR",
                            "location": "models/product_template.py:_resolve_value_and_type:string_empty",
                            "message": "export_resolve_branch",
                            "data": {"field": field_name, "branch": "string_empty"},
                        }
                    )
                    # endregion agent log
                    return None, mf_type
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_STR",
                        "location": "models/product_template.py:_resolve_value_and_type:string",
                        "message": "export_resolve_branch",
                        "data": {"field": field_name, "branch": "string"},
                    }
                )
                # endregion agent log
                return value, mf_type

            # FALLBACK
            try:
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_FALLBACK",
                        "location": "models/product_template.py:_resolve_value_and_type:fallback",
                        "message": "export_resolve_branch",
                        "data": {"field": field_name, "branch": "fallback"},
                    }
                )
                # endregion agent log
                return str(value), mf_type
            except Exception:
                # region agent log
                _agent_log(
                    {
                        "runId": "pre-fix",
                        "hypothesisId": "EXP_FALLBACK",
                        "location": "models/product_template.py:_resolve_value_and_type:fallback_exc",
                        "message": "export_resolve_exception",
                        "data": {"field": field_name, "branch": "fallback_exc"},
                    }
                )
                # endregion agent log
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
                _logger.info("[META DEBUG] Field=%s Raw Value=%s", field_name, "MISSING")
                continue

            raw_val = self[field_name]
            # Skip binary fields (images, attachments, etc.)
            if isinstance(raw_val, (bytes, bytearray)):
                _logger.warning(
                    "[META WARNING] Skipping binary field %s (not allowed in Shopify metafields)",
                    field_name,
                )
                _logger.info("[META DEBUG] Field=%s Raw Value=BINARY_DATA", field_name)
                continue
            # Skip Odoo recordsets (one2many/many2many/many2one values) not JSON-safe for Shopify
            if isinstance(raw_val, models.BaseModel):
                _logger.warning(
                    "[META WARNING] Skipping recordset field %s (not JSON-serializable for Shopify metafields)",
                    field_name,
                )
                _logger.info("[META DEBUG] Field=%s Raw Value=RECORDSET_DATA", field_name)
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

        _logger.info(
            "Prepared metafield keys: %s",
            [m.get("key") for m in metafields],
        )
        _logger.info("++++++++=========== META SYNC DEBUG END ===========++++++++")
        return metafields

