from odoo import api, fields, models, _


class SaleOrder(models.Model):
    _inherit = "sale.order"

    shopify_order_id = fields.Char(
        string="Shopify Order ID",
        index=True,
        help="ID of the corresponding order in Shopify used for idempotent imports.",
    )
    shopify_instance_id = fields.Many2one(
        "shopify.store",
        string="Shopify Store",
        index=True,
        help="Shopify store this order was imported from.",
    )
    shopify_fulfillment_status = fields.Char(
        string="Shopify Fulfillment Status",
        index=True,
        help="Raw fulfillment status from Shopify (e.g. fulfilled, partial, unfulfilled).",
    )
    shopify_fulfillment_ids = fields.Char(
        string="Shopify Fulfillment IDs",
        copy=False,
        index=True,
        help="Comma-separated Shopify fulfillment ids already applied (idempotency).",
    )
    shopify_fulfilled = fields.Boolean(
        string="Shopify Fulfilled",
        compute="_compute_shopify_fulfilled",
        store=True,
        index=True,
        help="True when this order's fulfillment/tracking has been successfully pushed to Shopify.",
    )
    shopify_fulfillment_mapping_failed = fields.Boolean(
        string="Fulfillment Mapping Failed",
        default=False,
        index=True,
        copy=False,
        help="A Shopify fulfillment line could not be safely mapped to an Odoo "
        "stock move. Flagged for manual review; no stock was delivered.",
    )
    shopify_fulfillment_reversal_flagged = fields.Boolean(
        string="Fulfillment Reversal Flagged",
        default=False,
        index=True,
        copy=False,
        help="A Shopify fulfillment was cancelled/reversed after the Odoo picking "
        "was already done. Flagged for controlled return/reversal; the completed "
        "picking is never auto-reverted.",
    )
    shopify_fulfillment_note = fields.Char(
        string="Fulfillment Review Note",
        copy=False,
        help="Human-readable note explaining a flagged fulfillment mapping/reversal case.",
    )

    def _compute_shopify_fulfilled(self):
        for order in self:
            order.shopify_fulfilled = order.shopify_fulfillment_status == "fulfilled"

    shopify_cancelled = fields.Boolean(
        string="Shopify Cancelled",
        default=False,
        index=True,
        help="True when this order was cancelled in Shopify.",
    )
    shopify_cancel_reason = fields.Char(
        string="Shopify Cancel Reason",
        help="Cancellation reason received from Shopify or sent from Odoo.",
    )

    shopify_refunded = fields.Boolean(
        string="Shopify Refunded",
        default=False,
        index=True,
        help="True when this order's refund has been synced between Odoo and Shopify.",
    )
    shopify_refund_date = fields.Datetime(
        string="Shopify Refund Date",
        help="Timestamp when the refund was synced/processed.",
        index=True,
    )
    shopify_refunded_amount = fields.Float(
        string="Shopify Refunded Amount",
        help="Cumulative refund amount synced from Shopify.",
    )
    shopify_payment_gateway = fields.Char(
        string="Shopify Payment Gateway",
        index=True,
        help="Primary payment gateway name from the Shopify order payload.",
    )
    shopify_order_total = fields.Float(
        string="Shopify Order Total",
        help="Total price from Shopify (gross).",
    )
    shopify_net_received = fields.Float(
        string="Shopify Net Received",
        help="Net amount received after payment gateway fees.",
    )
    shopify_amount_paid = fields.Float(
        string="Shopify Amount Paid",
        help="Cumulative amount registered from Shopify payments.",
    )
    shopify_gateway_fee = fields.Float(
        string="Shopify Gateway Fee",
        help="Computed payment gateway fee for this order.",
    )
    shopify_fee_discount_pending = fields.Float(
        string="Pending Fee Discount",
        help="Fee amount to apply as global discount at invoicing (invoice_discount mode).",
    )
    shopify_exchange_parent_id = fields.Many2one(
        "sale.order",
        string="Exchange Parent Order",
        ondelete="set null",
        index=True,
        help="Original order when this sale order is a replacement from an exchange.",
    )
    shopify_exchange_child_ids = fields.One2many(
        "sale.order",
        "shopify_exchange_parent_id",
        string="Exchange Replacement Orders",
    )
    shopify_exchange_price_diff = fields.Float(
        string="Exchange Price Difference",
        help="Replacement total minus returned total (positive = customer owes more).",
    )
    shopify_discount_source = fields.Char(
        string="Shopify Discount Source",
        help="Normalized discount sources from Shopify (coupon, automatic, unknown).",
        index=True,
    )
    shopify_exchange_key = fields.Char(
        string="Shopify Exchange Key",
        index=True,
        help="Idempotency key for exchange processing (e.g. exchange-<order_id>).",
    )
    shopify_sync_log_count = fields.Integer(
        compute="_compute_shopify_timeline_counts",
    )
    shopify_credit_note_count = fields.Integer(
        compute="_compute_shopify_timeline_counts",
    )

    def _compute_shopify_timeline_counts(self):
        Log = self.env["shopify.sync.log"].sudo()
        Move = self.env["account.move"].sudo()
        for order in self:
            domain_logs = []
            if order.shopify_order_id:
                domain_logs = [("shopify_id", "=", order.shopify_order_id)]
            elif order.shopify_instance_id:
                domain_logs = [("store_id", "=", order.shopify_instance_id.id)]
            order.shopify_sync_log_count = Log.search_count(domain_logs) if domain_logs else 0
            order.shopify_credit_note_count = Move.search_count(
                [
                    ("move_type", "=", "out_refund"),
                    ("invoice_origin", "=", order.name),
                ]
            )

    def action_shopify_order_timeline(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Shopify Order Timeline"),
            "res_model": "sale.order",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "context": {"shopify_timeline_mode": True},
        }

    def action_view_shopify_sync_logs(self):
        self.ensure_one()
        domain = [("store_id", "=", self.shopify_instance_id.id)] if self.shopify_instance_id else []
        if self.shopify_order_id:
            domain = [("shopify_id", "=", self.shopify_order_id)]
        return {
            "type": "ir.actions.act_window",
            "name": _("Shopify Sync Logs"),
            "res_model": "shopify.sync.log",
            "view_mode": "list,form",
            "domain": domain,
        }

    def action_view_shopify_credit_notes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Credit Notes"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [
                ("move_type", "=", "out_refund"),
                ("invoice_origin", "=", self.name),
            ],
        }

    def action_open_shopify_exchange_wizard(self):
        self.ensure_one()
        if not self.shopify_order_id or not self.shopify_instance_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Shopify Exchange"),
            "res_model": "shopify.exchange.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_store_id": self.shopify_instance_id.id,
            },
        }

    def action_open_shopify_cancel_wizard(self):
        self.ensure_one()
        if not self.shopify_order_id or not self.shopify_instance_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancel in Shopify"),
            "res_model": "shopify.cancel.order.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_store_id": self.shopify_instance_id.id,
            },
        }

    _sql_constraints = [
        (
            "shopify_order_unique",
            "unique(shopify_order_id)",
            "Shopify order already imported.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        Store = self.env["shopify.store"].sudo()
        for rec, vals in zip(records, vals_list):
            if not vals.get("shopify_order_id"):
                continue
            shopify_user_id = False
            sid = vals.get("shopify_instance_id")
            if sid:
                store = Store.browse(sid)
                if store.exists():
                    user = store._resolve_import_order_salesperson_user()
                    shopify_user_id = user.id if user else False
            if not shopify_user_id:
                from ..services.order_service import OrderService

                shopify_user_id = OrderService(self.env)._resolve_shopify_user_id(store=None)
            if shopify_user_id:
                rec.user_id = shopify_user_id
        return records

