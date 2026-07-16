import logging
import json

from odoo import _, fields
from odoo.exceptions import UserError

from . import lifecycle_logger as llog


_logger = logging.getLogger(__name__)


class OrderImportService:
    def __init__(self, env):
        self.env = env

    def _log(
        self,
        store,
        message,
        payload,
        status="success",
        queue_id=None,
        shopify_id=None,
        attempt=None,
        duration_ms=None,
        error_type=None,
    ):
        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="order",
            message=message,
            payload=payload,
            status=status,
            queue_id=queue_id,
            shopify_id=shopify_id,
            attempt=attempt,
            duration_ms=duration_ms,
            error_type=error_type,
        )

    def _extract_gateway_name(self, payload):
        """Extract the payment gateway identifier from the Shopify payload."""
        gateway = payload.get("gateway")
        if gateway:
            return gateway

        payment_gateway_names = payload.get("payment_gateway_names")
        # Shopify can send this either as a list or as a string
        if isinstance(payment_gateway_names, list) and payment_gateway_names:
            return payment_gateway_names[0]
        if isinstance(payment_gateway_names, str) and payment_gateway_names:
            return payment_gateway_names
        return False

    def _get_any_valid_workflow(self):
        """Return a generic active workflow record when available."""
        workflow_model_name = "sale.workflow.process"
        if workflow_model_name in self.env:
            workflow_model = self.env[workflow_model_name]
        else:
            workflow_model_name = "shopify.sale.auto.workflow"
            workflow_model = self.env[workflow_model_name]

        domain = []
        if "active" in workflow_model._fields:
            domain.append(("active", "=", True))
        workflow = workflow_model.search(domain, limit=1)
        if not workflow:
            return self._create_basic_workflow()
        return workflow, workflow_model_name

    def _create_basic_workflow(self):
        """Create a minimal workflow record in the first available workflow model."""
        workflow_model_name = "sale.workflow.process"
        if workflow_model_name in self.env:
            workflow_model = self.env[workflow_model_name]
        else:
            workflow_model_name = "shopify.sale.auto.workflow"
            workflow_model = self.env[workflow_model_name]

        values = {}
        if "name" in workflow_model._fields:
            values["name"] = "Shopify Auto Workflow"
        if "active" in workflow_model._fields:
            values["active"] = True

        workflow = workflow_model.create(values)

        _logger.warning(
            "Auto-created basic workflow in %s with id=%s",
            workflow_model_name,
            workflow.id,
        )
        return workflow, workflow_model_name

    def _get_or_create_any_valid_workflow(self):
        """Always return a workflow by searching first, then creating one."""
        workflow, workflow_model_name = self._get_any_valid_workflow()
        if workflow:
            return workflow, workflow_model_name
        _logger.warning(
            "No active workflow found in %s/%s, creating a basic fallback workflow.",
            "sale.workflow.process",
            "shopify.sale.auto.workflow",
        )
        return self._create_basic_workflow()

    def ensure_shopify_workflow_config(self, store):
        """Auto-heal Shopify workflow configuration for the given store."""
        if not store:
            _logger.warning("Auto-heal skipped: missing store.")
            return False

        Gateway = self.env["shopify.payment.gateway"]
        FinancialStatus = self.env["shopify.financial.status"]

        gateway = Gateway.search(
            [
                ("instance_id", "=", store.id),
                ("payment_code", "=ilike", "manual"),
            ],
            limit=1,
        )
        if not gateway:
            gateway = Gateway.create(
                {
                    "name": "Manual",
                    "instance_id": store.id,
                    "payment_code": "manual",
                    "active": True,
                }
            )
            _logger.warning(
                "Auto-heal created manual payment gateway for store=%s gateway_id=%s",
                store.id,
                gateway.id,
            )
        elif not gateway.active:
            gateway.write({"active": True})
            _logger.warning(
                "Auto-heal reactivated manual payment gateway for store=%s gateway_id=%s",
                store.id,
                gateway.id,
            )

        workflow = store.sale_auto_workflow_id
        if not workflow:
            workflow, workflow_model_name = self._get_or_create_any_valid_workflow()
            _logger.warning(
                "Auto-heal selected fallback workflow from %s id=%s for store=%s",
                workflow_model_name,
                workflow.id if workflow else False,
                store.id,
            )

        required_statuses = ("pending", "partially_paid", "paid")
        for status_code in required_statuses:
            mapping = FinancialStatus.search(
                [
                    ("instance_id", "=", store.id),
                    ("payment_gateway_id", "=", gateway.id),
                    ("shopify_financial_status", "=", status_code),
                ],
                limit=1,
            )
            if not mapping:
                mapping = FinancialStatus.create(
                    {
                        "instance_id": store.id,
                        "payment_gateway_id": gateway.id,
                        "shopify_financial_status": status_code,
                        "workflow_id": workflow.id,
                        "active": True,
                    }
                )
                _logger.warning(
                    "Auto-heal created financial status mapping store=%s status=%s mapping_id=%s workflow_id=%s",
                    store.id,
                    status_code,
                    mapping.id,
                    workflow.id,
                )
                continue

            write_vals = {}
            if not mapping.active:
                write_vals["active"] = True
            if not mapping.workflow_id:
                write_vals["workflow_id"] = workflow.id
            if write_vals:
                mapping.write(write_vals)
                _logger.warning(
                    "Auto-heal repaired financial mapping store=%s status=%s mapping_id=%s updates=%s",
                    store.id,
                    status_code,
                    mapping.id,
                    write_vals,
                )

        if not store.sale_auto_workflow_id:
            store.write({"sale_auto_workflow_id": workflow.id})
            _logger.warning(
                "Auto-heal assigned store fallback workflow store=%s workflow_id=%s",
                store.id,
                workflow.id,
            )

        _logger.warning("Store workflow configuration repaired for store=%s", store.id)
        return workflow

    def validate_shopify_workflow_config(self, store):
        """Validate mandatory Shopify workflow configuration and return debug data."""
        result = {
            "gateway_exists": False,
            "mappings": {
                "pending": False,
                "partially_paid": False,
                "paid": False,
            },
            "workflow_present": False,
            "store_fallback": False,
        }
        if not store:
            return result

        gateway = self.env["shopify.payment.gateway"].search(
            [
                ("instance_id", "=", store.id),
                ("active", "=", True),
                ("payment_code", "=ilike", "manual"),
            ],
            limit=1,
        )
        result["gateway_exists"] = bool(gateway)

        status_model = self.env["shopify.financial.status"]
        required_statuses = ("pending", "partially_paid", "paid")
        has_any_mapping_workflow = False
        for status_code in required_statuses:
            mapping = status_model.search(
                [
                    ("instance_id", "=", store.id),
                    ("payment_gateway_id", "=", gateway.id if gateway else False),
                    ("shopify_financial_status", "=", status_code),
                ],
                limit=1,
            )
            is_valid = bool(mapping and mapping.active and mapping.workflow_id)
            result["mappings"][status_code] = is_valid
            if is_valid:
                has_any_mapping_workflow = True

        result["workflow_present"] = bool(
            store.sale_auto_workflow_id or has_any_mapping_workflow or self._get_any_valid_workflow()[0]
        )
        result["store_fallback"] = bool(store.sale_auto_workflow_id)
        return result

    def import_shopify_order(
        self,
        store,
        order_data,
        queue=None,
        order_service=None,
        fulfillment_service=None,
        correlation_id=None,
    ):
        """Main Shopify order import wrapper used by the order queue worker."""
        order_data = order_data or {}
        shopify_order_id = order_data.get("id") or order_data.get("order_id") or False
        correlation_id = correlation_id or getattr(queue, "correlation_id", None)

        _logger.error("📦 Importing Shopify order: %s", shopify_order_id)
        try:
            _logger.error("Payload (truncated): %s", json.dumps(order_data)[:1000])
        except Exception:
            _logger.error("Payload (truncated): %s", str(order_data)[:1000])

        financial_status = order_data.get("financial_status")
        payment_gateway_names = order_data.get("payment_gateway_names")
        _logger.error("Financial status: %s", financial_status)
        _logger.error("Gateways: %s", payment_gateway_names)

        workflow = self.get_financial_workflow(store, order_data)
        _logger.error("Workflow found: %s", workflow)

        if not workflow:
            _logger.error(
                "No workflow available, cannot import Shopify order (queue_id=%s order_id=%s)",
                getattr(queue, "id", None),
                shopify_order_id,
            )
            return (
                False,
                _(
                    "No workflow available for this order. Configure either a "
                    "gateway/financial mapping, store sale_auto_workflow_id, or a global fallback workflow."
                ),
            )

        try:
            from .order_service import OrderService
            from .fulfillment_service import ShopifyFulfillmentService

            order_service = order_service or OrderService(self.env, import_service=self)
            fulfillment_service = fulfillment_service or ShopifyFulfillmentService(self.env)

            sale_order = order_service.create_order_from_payload(order_data, store)

            from .payment_fee_service import PaymentFeeService

            fee_service = PaymentFeeService(self.env, import_service=self)
            fee_service.apply_fees_for_order(sale_order, store, order_data)

            # P7: reconcile Shopify vs Odoo totals before invoicing/payment.
            self.log_total_reconciliation(
                sale_order, store, order_data, correlation_id=correlation_id
            )

            self.apply_workflow(
                sale_order, store, payload=order_data, workflow=workflow,
                correlation_id=correlation_id,
            )
            fulfillment_service.handle_fulfillment(
                sale_order, store, payload=order_data or {}, correlation_id=correlation_id
            )
            # Cron imports run as OdooBot; workflow/invoice steps may reset salesperson — enforce store mapping again.
            shop_user = store._resolve_import_order_salesperson_user()
            if shop_user:
                sale_order.sudo().write({"user_id": shop_user.id})
                if sale_order.invoice_ids:
                    sale_order.invoice_ids.sudo().write({"invoice_user_id": shop_user.id})

            _logger.error("✅ Order import completed (order_id=%s)", shopify_order_id)
            return True, False
        except Exception as e:
            _logger.exception("❌ Order import failed: %s", str(e))
            raise

    def log_total_reconciliation(self, order, store, payload, correlation_id=None):
        """Emit P7 reconciliation logs comparing Shopify vs Odoo amounts.

        Non-fatal: purely observability. Flags a mismatch beyond the 0.01
        rounding tolerance so operators can catch tax/shipping/discount drift.
        """
        payload = payload or {}
        trace = llog.LifecycleTrace(
            correlation_id=correlation_id,
            op=llog.OP_CREATE,
            shop_order=order.shopify_order_id,
            so=order.name,
        )

        def _f(value):
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        shopify_total = _f(payload.get("total_price"))
        shopify_tax = _f(payload.get("total_tax"))
        shopify_ship = sum(
            _f(s.get("discounted_price") if s.get("discounted_price") not in (None, "") else s.get("price"))
            for s in (payload.get("shipping_lines") or [])
            if not s.get("is_removed")
        )
        odoo_total = order.amount_total
        odoo_tax = order.amount_tax
        delivery_product = store.delivery_product_id if store else False
        odoo_ship = 0.0
        if delivery_product:
            odoo_ship = sum(
                l.price_subtotal
                for l in order.order_line
                if l.product_id.id == delivery_product.id
            )

        diff = round(odoo_total - shopify_total, 2)
        within_tolerance = abs(diff) <= 0.01

        trace.step(
            llog.STEP_TAX_MAPPING,
            so=order.name,
            status="ok" if abs(odoo_tax - shopify_tax) <= 0.01 else "warn",
            level=logging.INFO if abs(odoo_tax - shopify_tax) <= 0.01 else logging.WARNING,
            msg="shopify_tax=%s odoo_tax=%s taxes_included=%s"
            % (shopify_tax, odoo_tax, bool(payload.get("taxes_included"))),
        )
        trace.step(
            llog.STEP_SHIPPING_MAPPING,
            so=order.name,
            status="ok" if abs(odoo_ship - shopify_ship) <= 0.01 else "warn",
            level=logging.INFO if abs(odoo_ship - shopify_ship) <= 0.01 else logging.WARNING,
            msg="shopify_shipping=%s odoo_shipping=%s delivery_product=%s"
            % (shopify_ship, odoo_ship, bool(delivery_product)),
        )
        trace.step(
            llog.STEP_TOTAL_RECONCILIATION,
            so=order.name,
            status="ok" if within_tolerance else "warn",
            level=logging.INFO if within_tolerance else logging.WARNING,
            msg="shopify_total=%s odoo_total=%s diff=%s tolerance=0.01 within=%s"
            % (shopify_total, odoo_total, diff, within_tolerance),
        )
        return within_tolerance

    def get_financial_workflow(self, store, payload):
        """Resolve order workflow from Shopify payment metadata with hard fallbacks."""
        order_data = payload or {}
        order_id = order_data.get("id")

        gateway_list = order_data.get("payment_gateway_names") or []
        if isinstance(gateway_list, str):
            gateway_list = [gateway_list]
        gateway = gateway_list[0] if gateway_list else None
        financial_status = order_data.get("financial_status")

        def _normalize(value):
            return (value or "").strip().lower()

        normalized_status = _normalize(financial_status)
        normalized_gateways = []
        for gateway_name in gateway_list:
            normalized_name = _normalize(gateway_name)
            if normalized_name and normalized_name not in normalized_gateways:
                normalized_gateways.append(normalized_name)

        _logger.warning(
            "Shopify Workflow Resolution -> order=%s | raw_gateways=%s | first_gateway=%s | status=%s",
            order_id,
            gateway_list,
            gateway,
            financial_status,
        )
        _logger.info(
            "Shopify Workflow Normalized -> order=%s | gateways=%s | status=%s",
            order_id,
            normalized_gateways,
            normalized_status,
        )

        auto_heal_enabled = (
            str(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("shopify.auto_heal_workflow", default="True")
            ).strip().lower()
            in ("1", "true", "yes", "y", "on")
        )
        if auto_heal_enabled and store:
            _logger.warning(
                "Auto-heal requested for workflow resolution (order=%s store=%s).",
                order_id,
                store.id,
            )
            self.ensure_shopify_workflow_config(store)

        if not store:
            _logger.error(
                "Workflow resolution missing store instance for order=%s payload_keys=%s; forcing global workflow fallback.",
                order_id,
                sorted(order_data.keys()),
            )
            fallback_workflow, fallback_model_name = self._get_or_create_any_valid_workflow()
            _logger.warning(
                "Using global fallback workflow for missing store order=%s model=%s workflow_id=%s",
                order_id,
                fallback_model_name,
                fallback_workflow.id if fallback_workflow else False,
            )
            return fallback_workflow

        Gateway = self.env["shopify.payment.gateway"]
        FinancialStatus = self.env["shopify.financial.status"]
        in_dev_mode = bool(self.env.context.get("dev_mode"))

        def _find_workflow(gateway_code, status_code):
            if not gateway_code or not status_code:
                return False, {
                    "gateway_exists": False,
                    "mapping_exists": False,
                    "workflow_id_missing": False,
                }

            gateway_rec = Gateway.search(
                [
                    ("instance_id", "=", store.id),
                    ("active", "=", True),
                    ("payment_code", "=ilike", gateway_code),
                ],
                limit=1,
            )
            if not gateway_rec:
                return False, {
                    "gateway_exists": False,
                    "mapping_exists": False,
                    "workflow_id_missing": False,
                }

            rule = FinancialStatus.search(
                [
                    ("instance_id", "=", store.id),
                    ("payment_gateway_id", "=", gateway_rec.id),
                    ("shopify_financial_status", "=ilike", status_code),
                ("active", "=", True),
                ],
                limit=1,
            )
            workflow = (
                rule.workflow_id
                if rule and rule.active and rule.workflow_id
                else False
            )
            return workflow, {
                "gateway_exists": True,
                "gateway_id": gateway_rec.id,
                "mapping_exists": bool(rule),
                "mapping_id": rule.id if rule else False,
                "mapping_active": bool(rule and rule.active),
                "workflow_id_missing": bool(rule and not rule.workflow_id),
            }

        def _ensure_dev_mapping(gateway_code, status_code):
            if not in_dev_mode or not gateway_code or not status_code:
                return False

            default_workflow = store.sale_auto_workflow_id
            if not default_workflow:
                default_workflow, _ = self._get_any_valid_workflow()
            if not default_workflow:
                return False

            gateway_rec = Gateway.search(
                [
                    ("instance_id", "=", store.id),
                    ("payment_code", "=ilike", gateway_code),
                ],
                limit=1,
            )
            if not gateway_rec:
                gateway_rec = Gateway.create(
                    {
                        "name": gateway_code.title(),
                        "instance_id": store.id,
                        "payment_code": gateway_code,
                        "active": True,
                    }
                )

            mapping = FinancialStatus.search(
                [
                    ("instance_id", "=", store.id),
                    ("payment_gateway_id", "=", gateway_rec.id),
                    ("shopify_financial_status", "=", status_code),
                ],
                limit=1,
            )
            if not mapping:
                mapping = FinancialStatus.create(
                    {
                        "instance_id": store.id,
                        "payment_gateway_id": gateway_rec.id,
                        "shopify_financial_status": status_code,
                        "workflow_id": default_workflow.id,
                        "active": True,
                    }
                )
            elif not mapping.workflow_id:
                mapping.write({"workflow_id": default_workflow.id, "active": True})
            elif not mapping.active:
                mapping.write({"active": True})

            _logger.warning(
                "DEV MODE auto-healed workflow mapping for order=%s gateway=%s status=%s mapping=%s workflow=%s",
                order_id,
                gateway_code,
                status_code,
                mapping.id,
                mapping.workflow_id.id if mapping.workflow_id else False,
            )
            return mapping.workflow_id or False

        failure_snapshots = []

        # First try exact match against each gateway provided by Shopify.
        for gateway_code in normalized_gateways:
            workflow, snapshot = _find_workflow(gateway_code, normalized_status)
            if workflow:
                _logger.info(
                    "Workflow matched by exact gateway/status for order=%s gateway=%s status=%s workflow=%s",
                    order_id,
                    gateway_code,
                    normalized_status,
                    workflow.id,
                )
                return workflow
            snapshot.update(
                {
                    "order_id": order_id,
                    "gateway": gateway_code,
                    "financial_status": normalized_status,
                }
            )
            failure_snapshots.append(snapshot)
            _logger.info(
                "No workflow match for exact gateway/status on order=%s gateway=%s status=%s (mapping_exists=%s workflow_id_missing=%s)",
                order_id,
                gateway_code,
                normalized_status,
                snapshot.get("mapping_exists"),
                snapshot.get("workflow_id_missing"),
            )
            dev_workflow = _ensure_dev_mapping(gateway_code, normalized_status)
            if dev_workflow:
                return dev_workflow

        # If gateway is missing, fallback to configurable default gateway (manual by default).
        if not normalized_gateways:
            fallback_gateway = _normalize(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("shopify.default_payment_gateway", default="manual")
            ) or "manual"
            _logger.warning(
                "No payment gateway in Shopify payload for order=%s; using fallback gateway=%s",
                order_id,
                fallback_gateway,
            )
            workflow, snapshot = _find_workflow(fallback_gateway, normalized_status)
            if workflow:
                _logger.info(
                    "Workflow matched using fallback gateway for order=%s gateway=%s status=%s workflow=%s",
                    order_id,
                    fallback_gateway,
                    normalized_status,
                    workflow.id,
                )
                return workflow
            snapshot.update(
                {
                    "order_id": order_id,
                    "gateway": fallback_gateway,
                    "financial_status": normalized_status,
                }
            )
            failure_snapshots.append(snapshot)
            _logger.warning(
                "Fallback gateway did not resolve workflow for order=%s gateway=%s status=%s (mapping_exists=%s workflow_id_missing=%s)",
                order_id,
                fallback_gateway,
                normalized_status,
                snapshot.get("mapping_exists"),
                snapshot.get("workflow_id_missing"),
            )
            dev_workflow = _ensure_dev_mapping(fallback_gateway, normalized_status)
            if dev_workflow:
                return dev_workflow

        # Step A: store-level fallback workflow.
        if store.sale_auto_workflow_id:
            _logger.warning(
                "No gateway/status mapping found for order=%s; using store default workflow=%s",
                order_id,
                store.sale_auto_workflow_id.id,
            )
            return store.sale_auto_workflow_id

        # Step B: fallback to first active workflow from known workflow model.
        any_workflow, workflow_model_name = self._get_any_valid_workflow()
        if any_workflow:
            _logger.warning(
                "No mapping/store-default workflow found for order=%s; using first available workflow from %s id=%s",
                order_id,
                workflow_model_name,
                any_workflow.id,
            )
            return any_workflow

        # Step C: hard fallback: create or fetch any workflow and return it.
        validation = self.validate_shopify_workflow_config(store)
        latest_snapshot = failure_snapshots[-1] if failure_snapshots else {}
        _logger.warning(
            "Workflow mappings missing for order=%s | gateway=%s | status=%s | "
            "mapping_exists=%s | workflow_id_missing=%s | store_fallback=%s | validation=%s",
            order_id,
            latest_snapshot.get("gateway"),
            latest_snapshot.get("financial_status"),
            latest_snapshot.get("mapping_exists"),
            latest_snapshot.get("workflow_id_missing"),
            bool(store.sale_auto_workflow_id),
            validation,
        )
        fallback_workflow, fallback_model_name = self._get_or_create_any_valid_workflow()
        _logger.warning(
            "Hard fallback workflow used for order=%s model=%s workflow_id=%s",
            order_id,
            fallback_model_name,
            fallback_workflow.id if fallback_workflow else False,
        )
        return fallback_workflow

    def should_import_order(self, store, payload):
        """Return True if the order should be imported based on fulfillment status
        configuration on the Shopify store.
        """
        if not store:
            return False
        status_cfg = store.import_order_status or "unshipped"
        fulfillment_status = (payload or {}).get("fulfillment_status")
        if status_cfg == "unshipped":
            # Only orders where fulfillment_status is not set
            return not fulfillment_status
        # partially_fulfilled: allow None and 'partial'
        return fulfillment_status in (None, "partial")

    def _find_or_create_tax(self, store, tax_rate, name_hint=None, price_include=False):
        """Find an existing tax by percentage (and use_odoo_tax behavior) or
        create one when allowed by configuration.

        ``price_include`` selects a tax-inclusive tax (Shopify ``taxes_included``
        orders) versus a tax-exclusive tax. Matching an existing tax with the
        wrong inclusive behavior would silently shift totals, so it is part of
        the match key.
        """
        Tax = self.env["account.tax"]
        domain = [
            ("type_tax_use", "in", ["sale", "all"]),
            ("amount_type", "=", "percent"),
            ("amount", "=", tax_rate),
            ("company_id", "=", store.company_id.id),
        ]
        for tax in Tax.search(domain):
            if bool(tax.price_include) == bool(price_include):
                return tax

        if store.shopify_tax_behavior != "create_tax_if_not_found":
            # Do not silently apply a tax with the wrong inclusive behavior.
            return Tax.browse()

        name = name_hint or _("Shopify Tax %s%%") % tax_rate
        # Tax names are unique per company. A same-named tax with different
        # inclusive behavior (e.g. an existing tax-exclusive "GST") would clash,
        # so disambiguate the created tax name.
        if Tax.search_count(
            [("name", "=", name), ("company_id", "=", store.company_id.id)]
        ):
            name = "%s (%s)" % (name, "incl" if price_include else "excl")
        tax_group = self.env.ref("account.tax_group_taxes", raise_if_not_found=False)
        if not tax_group:
            tax_group = self.env["account.tax.group"].search(
                [("company_id", "in", [store.company_id.id, False])],
                limit=1,
            )
        if not tax_group:
            tax_group = self.env["account.tax.group"].create(
                {
                    "name": _("Shopify Taxes"),
                    "company_id": store.company_id.id,
                }
            )
        country = (
            store.company_id.account_fiscal_country_id
            or store.company_id.country_id
            or self.env.company.account_fiscal_country_id
            or self.env.company.country_id
        )
        if not country:
            country = self.env["res.country"].search([], limit=1)
        return Tax.create(
            {
                "name": name,
                "amount_type": "percent",
                "amount": tax_rate,
                "type_tax_use": "sale",
                "company_id": store.company_id.id,
                "tax_group_id": tax_group.id,
                "country_id": country.id,
                "price_include_override": "tax_included" if price_include else "tax_excluded",
            }
        )

    @staticmethod
    def _shopify_tax_line_amount(tax):
        """Return the actual tax amount charged for a single Shopify tax line."""
        amount = tax.get("price")
        if amount in (None, "") and isinstance(tax.get("price_set"), dict):
            shop_money = tax["price_set"].get("shop_money") or {}
            amount = shop_money.get("amount")
        try:
            return float(amount or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def get_taxes_for_line(self, store, line, taxes_included=None):
        """Return account.tax recordset for a Shopify line/shipping entry.

        P7 reconciliation rule: only apply an Odoo tax when Shopify actually
        charged tax on the entry. Shopify sends tax metadata (e.g. ``rate: 0.1``,
        ``title: GST``) even on non-taxable lines where the tax ``price`` is
        ``0.00`` and ``total_tax`` is ``0``. Mapping those to a percent tax made
        Odoo add ~10% VAT on top of the Shopify total — the dominant mismatch
        seen for orders #21573 and #21000. We therefore key the decision off the
        charged tax amount, not the rate alone.
        """
        Tax = self.env["account.tax"]
        if not store:
            return Tax.browse()

        tax_lines = line.get("tax_lines") or []
        if not tax_lines:
            return Tax.browse()

        # Shopify tax_lines can contain multiple entries; sum their rates and
        # the actual amounts charged.
        total_rate = 0.0
        total_tax_amount = 0.0
        name_hint = None
        for tax in tax_lines:
            try:
                total_rate += float(tax.get("rate") or 0.0) * 100.0
            except (TypeError, ValueError):
                pass
            total_tax_amount += self._shopify_tax_line_amount(tax)
            if not name_hint:
                name_hint = tax.get("title")

        # No rate, or Shopify charged no tax on this entry -> no Odoo tax.
        if total_rate <= 0.0 or total_tax_amount <= 0.0:
            return Tax.browse()

        if taxes_included is None:
            taxes_included = bool(line.get("_shopify_taxes_included"))

        return self._find_or_create_tax(
            store, total_rate, name_hint=name_hint, price_include=bool(taxes_included)
        )

    # ------------------------------------------------------------------
    # P5/P6 — gateway + financial-status decisioning
    # ------------------------------------------------------------------
    def _resolve_gateway_record(self, store, payload):
        gateway_name = self._extract_gateway_name(payload)
        if not (store and gateway_name):
            return self.env["shopify.payment.gateway"].browse()
        return self.env["shopify.payment.gateway"].search(
            [
                ("instance_id", "=", store.id),
                ("active", "=", True),
                ("payment_code", "=ilike", gateway_name.strip()),
            ],
            limit=1,
        )

    @staticmethod
    def _gateway_category(gateway, gateway_name):
        """Classify the gateway into a financial category for decisioning."""
        if gateway and gateway.gateway_type and gateway.gateway_type != "unknown":
            return gateway.gateway_type
        name = ((gateway.payment_code if gateway else "") or gateway_name or "").strip().lower()
        if not name:
            return "unknown"
        if any(k in name for k in ("cod", "cash_on_delivery", "cash on delivery")):
            return "cod"
        if any(k in name for k in ("paymob", "card", "credit", "wallet", "valu", "fawry")):
            return "online"
        if any(k in name for k in ("instapay", "bank", "transfer")):
            return "bank"
        if "manual" in name:
            return "manual"
        return "unknown"

    @staticmethod
    def _delivery_completed(order):
        pickings = order.picking_ids.filtered(
            lambda p: p.picking_type_code == "outgoing"
        )
        if not pickings:
            return False
        return all(p.state == "done" for p in pickings)

    def decide_financial_actions(self, order, store, payload, workflow):
        """Return an explicit, safe invoice/payment plan.

        Replaces the previous blanket behaviour (every order confirmed +
        invoiced + posted + paid regardless of gateway/status). Encodes the
        approved matrix; unknown/unmapped gateways fall back to a safe
        confirm-only plan and never register a payment or use a generic journal.
        """
        fin = (payload.get("financial_status") or "").strip().lower()
        gateway = self._resolve_gateway_record(store, payload)
        gateway_name = self._extract_gateway_name(payload) or (
            gateway.payment_code if gateway else False
        )
        category = self._gateway_category(gateway, gateway_name)

        from .payment_fee_service import PaymentFeeService

        fee_service = PaymentFeeService(self.env, import_service=self)
        txn_ids = fee_service.extract_sale_transaction_ids(payload or {})

        plan = {
            "gateway": gateway,
            "gateway_name": gateway_name,
            "category": category,
            "financial_status": fin,
            "txn_ids": txn_ids,
            "confirm": bool(workflow.confirm_quotation),
            "create_invoice": False,
            "post_invoice": False,
            "register_payment": False,
            "journal": gateway.odoo_journal_id if gateway else False,
            "reason": "",
            "safe_fallback": False,
            "config_error": False,
        }

        # Unknown / unmapped gateway -> safe confirm-only, flag config error.
        if category == "unknown" or not gateway:
            plan["safe_fallback"] = True
            plan["config_error"] = True
            plan["reason"] = "unmapped_gateway confirm_only"
            return plan

        # Payment evidence: successful Shopify transaction, or Shopify-confirmed
        # settlement (financial_status paid/partially_paid). Never gateway name
        # alone, order existence, or invoice posting.
        has_evidence = bool(txn_ids) or fin in ("paid", "partially_paid")

        want_invoice = bool(workflow.create_invoice)
        want_post = bool(workflow.validate_invoice)
        want_payment = bool(workflow.register_payment)

        if fin in ("refunded", "partially_refunded", "voided"):
            # Handled by refund/cancel flows; do not invoice or pay here.
            plan["reason"] = "no_invoice_no_payment status=%s" % (fin or "none")
            return plan

        if category == "cod":
            # COD: the invoice is driven by delivery completion (invoice_timing),
            # NOT by collection. The on_delivery gate below suppresses the invoice
            # until the Shopify fulfillment marks the Odoo picking done (P4). A
            # payment is only registered once collection is confirmed
            # (financial_status paid/partially_paid) with evidence.
            plan["create_invoice"] = want_invoice
            plan["post_invoice"] = want_post
            if fin in ("paid", "partially_paid"):
                plan["register_payment"] = want_payment
                plan["reason"] = "cod_collected"
            else:
                plan["reason"] = "cod_pending"
        elif category in ("online", "bank", "manual"):
            if fin in ("paid", "partially_paid"):
                plan["create_invoice"] = want_invoice
                plan["post_invoice"] = want_post
                plan["register_payment"] = want_payment
                plan["reason"] = "prepaid_paid" if fin == "paid" else "prepaid_partial"
            else:
                # authorized/pending/none -> not captured, do not invoice or pay.
                plan["reason"] = "prepaid_uncaptured confirm_only status=%s" % (fin or "none")
                return plan
        else:
            plan["safe_fallback"] = True
            plan["reason"] = "safe_confirm_only"
            return plan

        # Invoice timing: COD (or any workflow set to on_delivery) must wait for
        # a completed delivery before creating/posting the invoice.
        if workflow.invoice_timing == "on_delivery" and not self._delivery_completed(order):
            plan["create_invoice"] = False
            plan["post_invoice"] = False
            plan["reason"] = (plan["reason"] + " await_delivery").strip()

        # Payment safety gates (non-negotiable).
        if plan["register_payment"]:
            if workflow.require_payment_evidence and not has_evidence:
                plan["register_payment"] = False
                plan["reason"] = (plan["reason"] + " payment_skipped_no_evidence").strip()
            elif not plan["journal"]:
                # Known gateway but no journal configured -> never use a generic
                # fallback journal silently.
                plan["register_payment"] = False
                plan["config_error"] = True
                plan["reason"] = (plan["reason"] + " payment_skipped_no_journal").strip()

        return plan

    def apply_workflow(self, order, store, payload=None, workflow=None, correlation_id=None):
        """Apply the configured sales auto workflow on the sale order.

        If a workflow is provided (for example from financial status rules)
        it takes precedence. Otherwise falls back to store configuration.
        """
        workflow = workflow or store.sale_auto_workflow_id
        if not workflow or not order:
            return

        payload = payload or {}

        trace = llog.LifecycleTrace(
            correlation_id=correlation_id,
            op=llog.OP_CREATE,
            shop_order=order.shopify_order_id,
            so=order.name,
        )

        plan = self.decide_financial_actions(order, store, payload, workflow)

        if plan["config_error"]:
            trace.step(
                llog.STEP_GATEWAY_UNMAPPED,
                so=order.name,
                status="warn",
                level=logging.WARNING,
                msg="gateway=%s category=%s status=%s reason=%s"
                % (
                    plan["gateway_name"],
                    plan["category"],
                    plan["financial_status"] or "none",
                    plan["reason"],
                ),
            )
            self._log(
                store,
                _(
                    "Shopify gateway '%s' is not fully configured for order %s "
                    "(category=%s, status=%s): %s. Applying safe workflow; "
                    "no payment registered."
                )
                % (
                    plan["gateway_name"],
                    order.name,
                    plan["category"],
                    plan["financial_status"] or "none",
                    plan["reason"],
                ),
                {"order_id": order.id, "gateway": plan["gateway_name"]},
                "failed",
            )

        trace.step(
            llog.STEP_INVOICE_DECISION,
            so=order.name,
            msg="gateway=%s category=%s status=%s create_invoice=%s post_invoice=%s reason=%s"
            % (
                plan["gateway_name"],
                plan["category"],
                plan["financial_status"] or "none",
                plan["create_invoice"],
                plan["post_invoice"],
                plan["reason"],
            ),
        )

        # Apply shipment policy on the sale order
        if workflow.shipment_policy == "deliver_each_product":
            order.picking_policy = "direct"
        elif workflow.shipment_policy == "deliver_all_at_once":
            order.picking_policy = "one"

        payload_data = payload or {"order_id": order.id}

        # STEP 2: Confirm quotation
        if plan["confirm"] and order.state in ("draft", "sent"):
            try:
                order.action_confirm()
                self._log(
                    store,
                    _("Sale order %s confirmed by auto workflow.") % order.name,
                    payload_data,
                    "success",
                )
            except Exception as e:
                self._log(
                    store,
                    _("Failed to confirm sale order %s: %s") % (order.name, e),
                    payload_data,
                    "failed",
                )
                return

        invoices = self.env["account.move"]

        # STEP 3: Create invoice
        if plan["create_invoice"] and order.state in ("sale", "done"):
            existing_invoices = order.invoice_ids.filtered(
                lambda m: m.move_type == "out_invoice" and m.state != "cancel"
            )
            if existing_invoices:
                invoices = existing_invoices
            else:
                try:
                    invoices = order._create_invoices()

                    # Optionally force accounting date and sales journal
                    if invoices:
                        if workflow.force_accounting_date and order.date_order:
                            for inv in invoices:
                                inv.invoice_date = fields.Date.to_date(order.date_order)
                        if workflow.sales_journal_id:
                            invoices.write({"journal_id": workflow.sales_journal_id.id})

                    self._log(
                        store,
                        _("Invoice(s) created for sale order %s by auto workflow.")
                        % order.name,
                        {"order_id": order.id, "invoice_ids": invoices.ids},
                        "success",
                    )
                except Exception as e:
                    self._log(
                        store,
                        _("Failed to create invoices for sale order %s: %s")
                        % (order.name, e),
                        payload_data,
                        "failed",
                    )
                    return

        # STEP 4: Validate invoice
        if plan["post_invoice"] and invoices:
            invoices = invoices.filtered(lambda m: m.state == "draft")
            if invoices:
                try:
                    invoices.action_post()
                    self._log(
                        store,
                        _("Invoice(s) validated for sale order %s by auto workflow.")
                        % order.name,
                        {"order_id": order.id, "invoice_ids": invoices.ids},
                        "success",
                    )
                except Exception as e:
                    self._log(
                        store,
                        _("Failed to validate invoices for sale order %s: %s")
                        % (order.name, e),
                        {"order_id": order.id, "invoice_ids": invoices.ids},
                        "failed",
                    )
                    return

        posted_invoices = order.invoice_ids.filtered(
            lambda m: m.move_type == "out_invoice" and m.state == "posted"
        )

        # STEP 5: Register payment
        if not (plan["register_payment"] and posted_invoices):
            if posted_invoices and not plan["register_payment"]:
                trace.step(
                    llog.STEP_PAYMENT_SKIPPED,
                    so=order.name,
                    msg="reason=%s category=%s status=%s"
                    % (plan["reason"], plan["category"], plan["financial_status"] or "none"),
                )
        else:
            # Vetted by decide_financial_actions: evidence present + journal set.
            payment_journal = plan["journal"] or workflow.payment_journal_id
            payment_method = workflow.payment_method_id

            trace.step(
                llog.STEP_PAYMENT_DECISION,
                so=order.name,
                msg="posted_invoices=%s status=%s category=%s journal=%s"
                % (
                    len(posted_invoices),
                    plan["financial_status"] or "none",
                    plan["category"],
                    payment_journal.name if payment_journal else "none",
                ),
            )

            if not payment_journal:
                trace.step(
                    llog.STEP_PAYMENT_SKIPPED,
                    so=order.name,
                    status="warn",
                    level=logging.WARNING,
                    msg="no_journal category=%s" % plan["category"],
                )
                self._log(
                    store,
                    _(
                        "Payment registration skipped for sale order %s because "
                        "no journal is configured for gateway '%s'."
                    )
                    % (order.name, plan["gateway_name"]),
                    {"order_id": order.id, "invoice_ids": posted_invoices.ids},
                    "failed",
                )
                return

            try:
                from .payment_fee_service import PaymentFeeService

                fee_service = PaymentFeeService(self.env, import_service=self)
                financial_status = ((payload or {}).get("financial_status") or "").lower()
                partial_mode = store.partial_payment_mode or "register_paid_amount"

                if partial_mode == "skip_until_paid" and financial_status == "partially_paid":
                    trace.step(
                        llog.STEP_PAYMENT_SKIPPED,
                        so=order.name,
                        msg="partial_mode=skip_until_paid status=partially_paid",
                    )
                    self._log(
                        store,
                        _("Payment registration skipped for partially paid order %s.")
                        % order.name,
                        {"order_id": order.id},
                        "success",
                    )
                    return

                amount_residual = sum(posted_invoices.mapped("amount_residual"))
                if order.shopify_fee_discount_pending:
                    amount_residual = max(0.0, amount_residual - order.shopify_fee_discount_pending)

                if financial_status in ("partially_paid", "paid"):
                    target_paid = fee_service.extract_paid_amount(payload or {})
                    if order.shopify_fee_discount_pending and target_paid:
                        target_paid = max(0.0, target_paid - order.shopify_fee_discount_pending)
                    already_paid = order.shopify_amount_paid or 0.0
                    amount = min(max(0.0, target_paid - already_paid), amount_residual)
                else:
                    amount = amount_residual

                if not amount:
                    trace.step(
                        llog.STEP_PAYMENT_SKIPPED,
                        so=order.name,
                        msg="amount=0 residual=%s status=%s" % (amount_residual, financial_status),
                    )
                    return

                txn_ids = fee_service.extract_sale_transaction_ids(payload or {})
                Payment = self.env["account.payment"]
                for txn_id in txn_ids:
                    if Payment.search_count([("shopify_transaction_id", "=", txn_id)]):
                        trace.step(
                            llog.STEP_PAYMENT_SKIPPED,
                            so=order.name,
                            txn=txn_id,
                            msg="duplicate_transaction already_registered",
                        )
                        self._log(
                            store,
                            _("Payment already registered for Shopify transaction %s.") % txn_id,
                            {"order_id": order.id, "transaction_id": txn_id},
                            "success",
                        )
                        return

                payments = self.env["account.payment"]
                try:
                    method_line = payment_journal.inbound_payment_method_line_ids[:1]
                    if not method_line and payment_method:
                        method_line = payment_journal.inbound_payment_method_line_ids.filtered(
                            lambda l: l.payment_method_id == payment_method
                        )[:1]
                    register_wizard = (
                        self.env["account.payment.register"]
                        .with_context(active_model="account.move", active_ids=posted_invoices.ids)
                        .create(
                            {
                                "amount": amount,
                                "journal_id": payment_journal.id,
                                "payment_method_line_id": method_line.id if method_line else False,
                            }
                        )
                    )
                    payments = register_wizard._create_payments()
                except Exception:
                    payment_vals = {
                        "payment_type": "inbound",
                        "partner_type": "customer",
                        "partner_id": order.partner_id.id,
                        "amount": amount,
                        "currency_id": order.currency_id.id,
                        "journal_id": payment_journal.id,
                        "ref": _("Payment for Shopify order %s")
                        % (order.shopify_order_id or order.name),
                        "date": fields.Date.context_today(self.env.user),
                    }
                    if payment_method:
                        payment_vals["payment_method_id"] = payment_method.id
                    payments = Payment.create(payment_vals)
                    payments.action_post()

                shopify_txn_id = txn_ids[0] if txn_ids else False
                if payments and shopify_txn_id:
                    payments.write({"shopify_transaction_id": shopify_txn_id})

                order.write({"shopify_amount_paid": (order.shopify_amount_paid or 0.0) + amount})

                trace.step(
                    llog.STEP_PAYMENT_REGISTERED,
                    so=order.name,
                    txn=shopify_txn_id or None,
                    msg="amount=%s journal=%s category=%s status=%s"
                    % (amount, payment_journal.name, plan["category"], financial_status),
                )

                self._log(
                    store,
                    _("Payment registered for sale order %s by auto workflow (amount=%s).")
                    % (order.name, amount),
                    {
                        "order_id": order.id,
                        "invoice_ids": posted_invoices.ids,
                        "payment_ids": payments.ids if payments else [],
                        "amount": amount,
                    },
                    "success",
                )
            except Exception as e:
                self._log(
                    store,
                    _("Failed to register payment for sale order %s: %s")
                    % (order.name, e),
                    {"order_id": order.id, "invoice_ids": posted_invoices.ids},
                    "failed",
                )

    def trigger_invoice_on_delivery(self, order, store, correlation_id=None, financial_status=None):
        """P4/P5 — invoice-on-delivery hook.

        Called after a Shopify fulfillment marks the Odoo delivery done. Only
        acts for workflows configured with ``invoice_timing == 'on_delivery'``
        (COD): online/immediate workflows were already invoiced at import, so
        this is a safe no-op for them (idempotent, no duplicate invoice/payment).

        Payment is never registered here unless collection evidence exists, which
        it does not for a plain fulfillment event; ``decide_financial_actions``
        keeps payment gated. This proves *fulfillment completed != payment
        collected*.
        """
        if not (order and store) or not self._delivery_completed(order):
            return False

        # A plain fulfillment event carries no collection evidence, so default to
        # 'pending' (collection not confirmed). This resolves the COD gateway's
        # on-delivery workflow while keeping payment gated off.
        payload = {
            "id": order.shopify_order_id,
            "name": order.name,
            "financial_status": (financial_status or "pending").strip(),
            "gateway": order.shopify_payment_gateway or "",
            "payment_gateway_names": (
                [order.shopify_payment_gateway] if order.shopify_payment_gateway else []
            ),
        }

        workflow = self.get_financial_workflow(store, payload)
        if not workflow:
            return False
        # Only COD/on-delivery workflows invoice at delivery time. Immediate
        # workflows already invoiced at import; re-running is unnecessary and we
        # avoid touching already-paid online orders.
        if workflow.invoice_timing != "on_delivery":
            return False

        self.apply_workflow(
            order, store, payload=payload, workflow=workflow, correlation_id=correlation_id
        )
        return True

