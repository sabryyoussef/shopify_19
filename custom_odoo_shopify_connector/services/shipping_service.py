# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging

from odoo import _

from .shopify_fulfillment_service import ShopifyFulfillmentService

_logger = logging.getLogger(__name__)


class ShopifyShippingService:
    def __init__(self, env):
        self.env = env

    def _get_tracking_company(self, carrier):
        """Return Shopify tracking_company string (Char on delivery.carrier)."""
        if not carrier:
            return None
        if getattr(carrier, "shopify_tracking_company", None) and carrier.shopify_tracking_company:
            return (carrier.shopify_tracking_company or "").strip()
        return (carrier.name or _("Carrier") or "").strip()

    def _get_tracking_payloads(self, picking, default_carrier):
        """
        Build list of (tracking_number, carrier) for each fulfillment.
        Supports single picking tracking and multiple packages (stock.package or
        stock.quant.package) with carrier_tracking_ref. Each package sends a
        separate fulfillment to Shopify.
        """
        result = []
        move_lines = getattr(picking, "move_line_ids", None) or []
        packages = move_lines.mapped("result_package_id")
        seen = set()
        for pkg in packages:
            if not pkg or pkg.id in seen:
                continue
            seen.add(pkg.id)
            tracking = getattr(pkg, "carrier_tracking_ref", None) or ""
            if not (tracking and (tracking := str(tracking).strip())):
                continue
            pcarrier = getattr(pkg, "carrier_id", None) and pkg.carrier_id or default_carrier
            if pcarrier:
                result.append((tracking, pcarrier))
        # Single tracking on picking when no package-level tracking
        if not result:
            tracking = (picking.carrier_tracking_ref or "").strip()
            if tracking and default_carrier:
                result.append((tracking, default_carrier))
        return result

    def update_shipping(self, picking, sale, store):
        """Send shipping / tracking information from Odoo delivery to Shopify (fulfillment API)."""
        if not (picking and sale and store and sale.shopify_order_id):
            return

        api_client = store._get_api_client()
        default_carrier = picking.carrier_id
        payloads = self._get_tracking_payloads(picking, default_carrier)
        if not payloads:
            return

        fulfillment_svc = ShopifyFulfillmentService(self.env)
        log_mixin = self.env["shopify.sync.log.mixin"]
        order_id = sale.shopify_order_id
        all_success = True

        for tracking_number, carrier in payloads:
            try:
                response = fulfillment_svc.create_fulfillment(
                    api_client,
                    order_id,
                    tracking_number,
                    carrier,
                    notify_customer=True,
                )
                try:
                    log_mixin.create_log(
                        store=store,
                        log_type="fulfillment",
                        message=_("Fulfillment created for order %s") % order_id,
                        payload=json.dumps({"tracking_number": tracking_number}),
                        status="success",
                        response=json.dumps(response)[:5000] if response else "",
                        order_id=order_id,
                    )
                except Exception:
                    pass
            except Exception as e:
                all_success = False
                picking.shopify_shipping_status = "failed"
                try:
                    sale.shopify_fulfillment_status = "failed"
                except Exception:
                    pass
                try:
                    log_mixin.create_log(
                        store=store,
                        log_type="fulfillment",
                        message=str(e),
                        payload=json.dumps({"tracking_number": tracking_number, "order_id": order_id}),
                        status="failed",
                        response=str(e),
                        order_id=order_id,
                    )
                except Exception:
                    pass
                _logger.warning("Shopify fulfillment failed for order %s: %s", order_id, e)
                continue

        if all_success and payloads:
            picking.shopify_shipping_status = "done"
            try:
                sale.shopify_fulfillment_status = "fulfilled"
            except Exception:
                pass
