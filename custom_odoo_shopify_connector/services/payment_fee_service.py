import logging

from odoo import _
from odoo.tools import float_round

_logger = logging.getLogger(__name__)


class PaymentFeeService:
    """Compute and apply Shopify payment gateway fees on sale orders."""

    def __init__(self, env, import_service=None):
        self.env = env
        self.import_service = import_service

    def resolve_gateway(self, store, payload):
        if not store:
            return self.env["shopify.payment.gateway"]

        gateway_name = False
        if self.import_service:
            gateway_name = self.import_service._extract_gateway_name(payload)
        if not gateway_name:
            gateway_name = payload.get("gateway")
        if not gateway_name:
            names = payload.get("payment_gateway_names") or []
            if isinstance(names, list) and names:
                gateway_name = names[0]
            elif isinstance(names, str):
                gateway_name = names

        if not gateway_name:
            return self.env["shopify.payment.gateway"]

        return self.env["shopify.payment.gateway"].search(
            [
                ("instance_id", "=", store.id),
                ("active", "=", True),
                ("payment_code", "ilike", gateway_name.strip()),
            ],
            limit=1,
        )

    def _fee_base_amount(self, order, payload, fee_base):
        if fee_base == "order_total":
            total = payload.get("total_price")
            if total is not None:
                return float(total or 0.0)
            return order.amount_total
        return sum(
            line.price_unit * line.product_uom_qty * (1 - (line.discount or 0.0) / 100.0)
            for line in order.order_line
            if not line.display_type
        )

    def compute_fee(self, order, gateway, payload):
        if not gateway or gateway.fee_apply_mode == "none":
            return 0.0
        percent = float(gateway.fee_percent or 0.0)
        fixed = float(gateway.fee_fixed or 0.0)
        if percent <= 0.0 and fixed <= 0.0:
            return 0.0
        base = self._fee_base_amount(order, payload, gateway.fee_base or "subtotal")
        fee = base * (percent / 100.0) + fixed
        return float_round(fee, 2)

    def _extract_order_totals(self, payload, fee_amount):
        order_total = float(payload.get("total_price") or 0.0)
        net_received = order_total - fee_amount
        transactions = payload.get("transactions") or []
        for txn in transactions:
            if (txn.get("kind") or "").lower() == "sale" and txn.get("status") == "success":
                try:
                    net_received = float(txn.get("amount") or net_received)
                except (TypeError, ValueError):
                    pass
                break
        return order_total, float_round(net_received, 2)

    def apply_fee_to_order(self, order, fee_amount, gateway, store, payload):
        order.write(
            {
                "shopify_gateway_fee": fee_amount,
                "shopify_order_total": self._extract_order_totals(payload, fee_amount)[0],
                "shopify_net_received": self._extract_order_totals(payload, fee_amount)[1],
            }
        )
        if fee_amount <= 0.0 or not gateway:
            return

        mode = gateway.fee_apply_mode
        if mode == "invoice_discount":
            order.write({"shopify_fee_discount_pending": fee_amount})
            return

        if mode != "line_item":
            return

        product = gateway.fee_product_id or store.payment_fee_product_id
        if not product:
            _logger.warning(
                "Payment fee skipped for order %s: no fee product configured.",
                order.name,
            )
            return

        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.id,
                "name": _("Payment Fees (%s)") % (gateway.name or gateway.payment_code),
                "product_uom_qty": 1.0,
                "price_unit": fee_amount,
            }
        )

    def apply_fees_for_order(self, order, store, payload):
        gateway = self.resolve_gateway(store, payload)
        gateway_name = self.import_service._extract_gateway_name(payload) if self.import_service else False
        if gateway_name:
            order.write({"shopify_payment_gateway": gateway_name})

        fee_amount = self.compute_fee(order, gateway, payload)
        order_total, net_received = self._extract_order_totals(payload, fee_amount)
        order.write(
            {
                "shopify_order_total": order_total,
                "shopify_net_received": net_received,
                "shopify_gateway_fee": fee_amount,
            }
        )
        if gateway:
            self.apply_fee_to_order(order, fee_amount, gateway, store, payload)
