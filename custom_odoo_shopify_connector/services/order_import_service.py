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

    def _find_or_create_tax(self, store, tax_rate, name_hint=None):
        """Find an existing tax by percentage (and use_odoo_tax behavior) or
        create one when allowed by configuration.
        """
        Tax = self.env["account.tax"]
        domain = [
            ("type_tax_use", "in", ["sale", "all"]),
            ("amount_type", "=", "percent"),
            ("amount", "=", tax_rate),
            ("company_id", "=", store.company_id.id),
        ]
        existing = Tax.search(domain, limit=1)
        if existing:
            return existing

        if store.shopify_tax_behavior != "create_tax_if_not_found":
            return Tax.browse()

        name = name_hint or _("Shopify Tax %s%%") % tax_rate
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
            }
        )

    def get_taxes_for_line(self, store, line):
        """Return account.tax recordset for a given Shopify line item according
        to the configured tax behavior.
        """
        Tax = self.env["account.tax"]
        if not store:
            return Tax.browse()

        tax_lines = line.get("tax_lines") or []
        if not tax_lines:
            return Tax.browse()

        # Shopify tax_lines can contain multiple entries; sum their rates
        total_rate = 0.0
        name_hint = None
        for tax in tax_lines:
            rate = float(tax.get("rate") or 0.0) * 100.0
            total_rate += rate
            if not name_hint:
                name_hint = tax.get("title")

        if not total_rate:
            return Tax.browse()

        return self._find_or_create_tax(store, total_rate, name_hint=name_hint)

    def apply_workflow(self, order, store, payload=None, workflow=None, correlation_id=None):
        """Apply the configured sales auto workflow on the sale order.

        If a workflow is provided (for example from financial status rules)
        it takes precedence. Otherwise falls back to store configuration.
        """
        workflow = workflow or store.sale_auto_workflow_id
        if not workflow or not order:
            return

        trace = llog.LifecycleTrace(
            correlation_id=correlation_id,
            op=llog.OP_CREATE,
            shop_order=order.shopify_order_id,
            so=order.name,
        )
        trace.step(
            llog.STEP_INVOICE_DECISION,
            so=order.name,
            msg="create_invoice=%s validate_invoice=%s register_payment=%s"
            % (workflow.create_invoice, workflow.validate_invoice, workflow.register_payment),
        )

        # Apply shipment policy on the sale order
        if workflow.shipment_policy == "deliver_each_product":
            order.picking_policy = "direct"
        elif workflow.shipment_policy == "deliver_all_at_once":
            order.picking_policy = "one"

        payload_data = payload or {"order_id": order.id}

        # STEP 2: Confirm quotation
        if workflow.confirm_quotation and order.state in ("draft", "sent"):
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
        if workflow.create_invoice and order.state in ("sale", "done"):
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
        if workflow.validate_invoice and invoices:
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
        if workflow.register_payment and posted_invoices:
            trace.step(
                llog.STEP_PAYMENT_DECISION,
                so=order.name,
                msg="posted_invoices=%s financial_status=%s"
                % (len(posted_invoices), (payload or {}).get("financial_status")),
            )
            payment_journal = workflow.payment_journal_id
            payment_method = workflow.payment_method_id

            # Prefer payment journal configured on the Shopify payment gateway
            # when the order is fully paid and a matching gateway configuration exists.
            if payload:
                gateway_name = self._extract_gateway_name(payload) or False
                if gateway_name:
                    gateway = (
                        self.env["shopify.payment.gateway"]
                        .search(
                            [
                                ("instance_id", "=", store.id),
                                ("active", "=", True),
                                ("payment_code", "ilike", gateway_name.strip()),
                            ],
                            limit=1,
                        )
                    )
                    if gateway and gateway.odoo_journal_id:
                        payment_journal = gateway.odoo_journal_id
                    elif gateway_name:
                        self._log(
                            store,
                            _(
                                "Payment journal fallback for gateway '%s' on order %s; "
                                "configure odoo_journal_id on the payment gateway."
                            )
                            % (gateway_name, order.name),
                            {"order_id": order.id, "gateway": gateway_name},
                            "success",
                        )

            if not payment_journal:
                self._log(
                    store,
                    _(
                        "Payment registration skipped for sale order %s because "
                        "payment journal is not configured on the workflow."
                    )
                    % order.name,
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
                    return

                txn_ids = fee_service.extract_sale_transaction_ids(payload or {})
                Payment = self.env["account.payment"]
                for txn_id in txn_ids:
                    if Payment.search_count([("shopify_transaction_id", "=", txn_id)]):
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

