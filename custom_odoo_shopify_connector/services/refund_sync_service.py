import logging

from odoo import _, fields
from odoo.tools import float_round

_logger = logging.getLogger(__name__)


class RefundSyncService:
    """Sync Shopify refunds to Odoo partial or full credit notes."""

    def __init__(self, env):
        self.env = env

    def _log(self, store, message, payload, status="success", order_id=False):
        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="refund",
            message=message,
            payload=payload,
            status=status,
            order_id=order_id,
        )

    def _find_posted_invoice(self, order):
        return self.env["account.move"].search(
            [
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("invoice_origin", "=", order.name),
            ],
            limit=1,
        )

    def _refund_already_processed(self, refund_id):
        if not refund_id:
            return False
        return bool(
            self.env["account.move"].search_count([("shopify_refund_id", "=", refund_id)])
        )

    def _map_refund_lines(self, order, refund_line_items):
        """Return list of dicts: sale_line, qty, amount."""
        mapped = []
        for rli in refund_line_items or []:
            line_item_id = str(rli.get("line_item_id") or "")
            qty = float(rli.get("quantity") or 0.0)
            if qty <= 0:
                continue
            subtotal = float(rli.get("subtotal") or 0.0)
            sale_line = order.order_line.filtered(
                lambda l: l.shopify_line_item_id == line_item_id and not l.display_type
            )[:1]
            if not sale_line and line_item_id:
                sale_line = order.order_line.filtered(
                    lambda l: str(l.id) == line_item_id and not l.display_type
                )[:1]
            mapped.append(
                {
                    "sale_line": sale_line,
                    "qty": qty,
                    "amount": subtotal,
                    "line_item_id": line_item_id,
                }
            )
        return mapped

    def _create_partial_credit_note(self, store, order, invoice, refund_payload, mapped_lines):
        refund_id = str(refund_payload.get("id") or "")
        line_commands = []
        for entry in mapped_lines:
            sale_line = entry["sale_line"]
            qty = entry["qty"]
            if sale_line:
                inv_line = invoice.invoice_line_ids.filtered(
                    lambda l: l.product_id == sale_line.product_id
                    and l.name == sale_line.name
                )[:1]
                if not inv_line:
                    inv_line = invoice.invoice_line_ids.filtered(
                        lambda l: l.product_id == sale_line.product_id
                    )[:1]
                price = entry["amount"] / qty if qty else sale_line.price_unit
                line_commands.append(
                    (
                        0,
                        0,
                        {
                            "product_id": sale_line.product_id.id,
                            "name": sale_line.name,
                            "quantity": qty,
                            "price_unit": float_round(price, 2),
                            "tax_ids": [(6, 0, (inv_line.tax_ids if inv_line else sale_line.tax_id).ids)],
                        },
                    )
                )
                sale_line.write(
                    {"shopify_refunded_qty": (sale_line.shopify_refunded_qty or 0.0) + qty}
                )
            elif entry["amount"] > 0:
                line_commands.append(
                    (
                        0,
                        0,
                        {
                            "name": _("Shopify refund line %s") % entry["line_item_id"],
                            "quantity": 1,
                            "price_unit": entry["amount"],
                        },
                    )
                )

        shipping_amount = float(
            (refund_payload.get("shipping") or {}).get("amount")
            or refund_payload.get("shipping_amount")
            or 0.0
        )
        if shipping_amount > 0:
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "name": _("Shipping refund"),
                        "quantity": 1,
                        "price_unit": shipping_amount,
                    },
                )
            )

        if not line_commands:
            return self.env["account.move"]

        credit = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": invoice.partner_id.id,
                "journal_id": invoice.journal_id.id,
                "invoice_origin": order.name,
                "ref": _("Shopify refund %s") % (refund_id or order.shopify_order_id),
                "reversed_entry_id": invoice.id,
                "invoice_line_ids": line_commands,
            }
        )
        credit.action_post()
        credit.write(
            {
                "shopify_refunded": True,
                "shopify_refund_date": fields.Datetime.now(),
                "shopify_refund_id": refund_id or False,
            }
        )
        refund_total = sum(entry["amount"] for entry in mapped_lines) + shipping_amount
        order.write(
            {
                "shopify_refunded_amount": (order.shopify_refunded_amount or 0.0) + refund_total,
                "shopify_refunded": True,
                "shopify_refund_date": fields.Datetime.now(),
            }
        )
        self._log(
            store,
            _("Partial credit note %s created for Shopify order %s.")
            % (credit.name, order.shopify_order_id),
            {"credit_note": credit.name, "refund_id": refund_id},
            order_id=order.shopify_order_id,
        )
        return credit

    def _create_full_reversal(self, store, order, invoice, refund_payload):
        refund_id = str(refund_payload.get("id") or "")
        credit = invoice._reverse_moves(
            default_values_list=[
                {"ref": _("Shopify refund %s") % (refund_id or order.shopify_order_id)}
            ]
        )
        if credit:
            credit.action_post()
            credit.write(
                {
                    "shopify_refunded": True,
                    "shopify_refund_date": fields.Datetime.now(),
                    "shopify_refund_id": refund_id or False,
                }
            )
            order.write(
                {
                    "shopify_refunded": True,
                    "shopify_refund_date": fields.Datetime.now(),
                    "shopify_refunded_amount": order.amount_total,
                }
            )
            self._log(
                store,
                _("Full credit note %s created for Shopify order %s.")
                % (credit.name, order.shopify_order_id),
                {"credit_note": credit.name, "refund_id": refund_id},
                order_id=order.shopify_order_id,
            )
        return credit

    def sync_refund_from_webhook(self, store, order, refund_payload):
        refund_id = str(refund_payload.get("id") or "")
        if self._refund_already_processed(refund_id):
            return self.env["account.move"]

        invoice = self._find_posted_invoice(order)
        if not invoice:
            self._log(
                store,
                _("Refund webhook skipped: no posted invoice for order %s.") % order.name,
                refund_payload,
                status="failed",
                order_id=order.shopify_order_id,
            )
            return self.env["account.move"]

        mode = store.refund_sync_mode or "line_level"
        refund_line_items = refund_payload.get("refund_line_items") or []

        if mode == "full" or not refund_line_items:
            credit = self._create_full_reversal(store, order, invoice, refund_payload)
        else:
            mapped = self._map_refund_lines(order, refund_line_items)
            if not mapped:
                credit = self._create_full_reversal(store, order, invoice, refund_payload)
            else:
                credit = self._create_partial_credit_note(
                    store, order, invoice, refund_payload, mapped
                )
                if credit and store.refund_restock_mode in ("odoo_restock", "both"):
                    from .return_picking_service import ReturnPickingService

                    ReturnPickingService(self.env).create_return_from_refund(
                        store, order, refund_payload, mapped
                    )
        return credit

    def sync_cancel_reversal(self, store, order, cancel_reason=None):
        """Create credit note for posted invoice when Shopify order is cancelled."""
        if not store or not order:
            return self.env["account.move"]
        if (store.cancel_sync_mode or "credit_note") == "cancel_only":
            return self.env["account.move"]

        cancel_refund_id = "cancel-%s" % (order.shopify_order_id or order.id)
        if self._refund_already_processed(cancel_refund_id):
            return self.env["account.move"]

        invoice = self._find_posted_invoice(order)
        if not invoice:
            return self.env["account.move"]

        refund_payload = {
            "id": cancel_refund_id,
            "note": cancel_reason or _("Shopify order cancelled"),
        }
        credit = self._create_full_reversal(store, order, invoice, refund_payload)
        if credit:
            self._log(
                store,
                _("Credit note %s created for cancelled Shopify order %s.")
                % (credit.name, order.shopify_order_id),
                {"credit_note": credit.name, "reason": cancel_reason},
                order_id=order.shopify_order_id,
            )
        return credit

    def cancel_order_from_shopify(self, store, order, cancel_reason=None):
        """
        Cancel an Odoo sale order in response to Shopify cancellation.
        - Cancels draft invoices (no CN).
        - Cancels open/unfinished pickings.
        - Creates CN only when a posted invoice exists (via sync_cancel_reversal).
        - Cancels the sale order itself.
        Idempotent for already-cancelled orders.
        """
        if not order:
            return False
        if order.state == "cancel" or order.shopify_cancelled:
            return True

        # Cancel draft invoices safely (never create CN for drafts)
        for invoice in order.invoice_ids.filtered(lambda m: m.state == "draft"):
            try:
                invoice.button_cancel()
            except Exception:
                invoice.write({"state": "cancel"})

        # Cancel unfinished stock operations
        for picking in order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
            try:
                picking.action_cancel()
            except Exception:
                picking.write({"state": "cancel"})

        # CN only if posted invoice exists
        self.sync_cancel_reversal(store, order, cancel_reason)

        if order.state != "cancel":
            try:
                order.action_cancel()
            except Exception:
                order.write({"state": "cancel"})

        order.write(
            {
                "shopify_cancelled": True,
                "shopify_cancel_reason": cancel_reason or "shopify",
            }
        )
        self._log(
            store,
            _("Odoo sale order %s cancelled from Shopify.") % order.name,
            {"odoo_order": order.name, "reason": cancel_reason},
            order_id=order.shopify_order_id,
        )
        return True

    def sync_refund_from_order_payload(self, store, order, payload):
        """Handle partially_refunded / refunded on fulfilled order import."""
        financial_status = (payload.get("financial_status") or "").lower()
        if financial_status not in ("refunded", "partially_refunded"):
            return self.env["account.move"]

        refunds = payload.get("refunds") or []
        credits = self.env["account.move"]
        if refunds:
            for refund in refunds:
                credits |= self.sync_refund_from_webhook(store, order, refund)
        elif financial_status == "refunded":
            invoice = self._find_posted_invoice(order)
            if invoice:
                credits = self._create_full_reversal(store, order, invoice, {})
        return credits

    def build_shopify_refund_payload(self, credit_note, sale_order):
        """Build Shopify Refunds API payload from Odoo credit note lines."""
        store = sale_order.shopify_instance_id
        restock_type = (store.outbound_refund_restock_type if store else "no_restock") or "no_restock"
        refund_line_items = []
        for inv_line in credit_note.invoice_line_ids.filtered(lambda l: not l.display_type):
            sale_line = sale_order.order_line.filtered(
                lambda l: l.product_id == inv_line.product_id and l.shopify_line_item_id
            )[:1]
            if not sale_line:
                continue
            refund_line_items.append(
                {
                    "line_item_id": int(sale_line.shopify_line_item_id),
                    "quantity": int(inv_line.quantity),
                    "restock_type": restock_type,
                }
            )

        amount = credit_note.amount_total
        payload = {
            "refund": {
                "notify": True,
                "note": credit_note.ref or "",
                "refund_line_items": refund_line_items,
                "transactions": [
                    {
                        "kind": "refund",
                        "amount": str(amount),
                        "gateway": sale_order.shopify_payment_gateway or "manual",
                    }
                ],
            }
        }
        if not refund_line_items:
            payload["refund"].pop("refund_line_items", None)
        return payload
