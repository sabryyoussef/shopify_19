from odoo import _, api, fields, models


class ShopifyStockImportQueue(models.Model):
    _name = "shopify.stock.import.queue"
    _description = "Shopify Stock Import Queue"
    _order = "create_date desc"

    name = fields.Char(
        string="Queue Reference",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )
    store_id = fields.Many2one(
        "shopify.store",
        string="Store",
        required=True,
        ondelete="cascade",
    )
    operation_type = fields.Selection(
        [
            ("import_stock", "Import Stock"),
        ],
        string="Operation Type",
        default="import_stock",
        required=True,
    )
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="Status",
        default="pending",
        required=True,
    )
    records_to_process = fields.Integer(string="Records To Process")
    processed_records = fields.Integer(string="Processed Records")
    error_message = fields.Text(string="Error Message")
    retry_count = fields.Integer(string="Retry Count", default=0, index=True)
    next_retry_at = fields.Datetime(string="Next Retry At", index=True)
    last_error = fields.Text(string="Last Error")

    line_ids = fields.One2many(
        "shopify.stock.import.queue.line",
        "queue_id",
        string="Stock Import Lines",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shopify.stock.import.queue"
                ) or _("New")
        return super().create(vals_list)

    def _process_queue(self, auto_apply_inventory_adjustment=True, batch_size=100):
        """Process inventory import lines in batches."""
        for queue in self:
            queue.status = "processing"
            pending_lines = queue.line_ids.filtered(
                lambda l: l.state in ("pending", "retry")
            )[:batch_size]
            for line in pending_lines:
                line._process_line(auto_apply_inventory_adjustment=auto_apply_inventory_adjustment)
                queue.processed_records = len(
                    queue.line_ids.filtered(lambda l: l.state in ("done", "skipped"))
                )

            if all(l.state in ("done", "skipped") for l in queue.line_ids):
                queue.status = "done"
            elif any(l.state == "failed" for l in queue.line_ids):
                queue.status = "failed"
            else:
                queue.status = "processing"


class ShopifyStockImportQueueLine(models.Model):
    _name = "shopify.stock.import.queue.line"
    _description = "Shopify Stock Import Queue Line"

    queue_id = fields.Many2one(
        "shopify.stock.import.queue",
        string="Queue",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        ondelete="cascade",
    )
    inventory_item_id = fields.Char(
        string="Shopify Inventory Item ID",
    )
    shopify_location_id = fields.Char(
        string="Shopify Location ID",
    )
    shopify_available_qty = fields.Float(
        string="Shopify Available Quantity",
    )
    odoo_qty = fields.Float(
        string="Odoo Quantity",
    )
    difference_qty = fields.Float(
        string="Difference",
        help="Shopify quantity minus Odoo quantity.",
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("retry", "Retry"),
            ("done", "Done"),
            ("skipped", "Skipped"),
            ("failed", "Failed"),
        ],
        string="Status",
        default="pending",
        required=True,
    )
    message = fields.Text(string="Message")

    def _process_line(self, auto_apply_inventory_adjustment=True):
        """Apply inventory adjustment for this line if needed."""
        self.ensure_one()
        product = self.product_id
        store = self.queue_id.store_id

        # Lot/serial tracking: skip
        if product.tracking and product.tracking != "none":
            self.state = "skipped"
            self.message = _(
                "Skipped product %s because tracking is '%s' (lot/serial not supported for stock import)."
            ) % (product.display_name, product.tracking)
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="inventory",
                message=self.message,
                payload="",
                status="success",
            )
            return

        # Only adjust if Shopify != Odoo
        if self.difference_qty == 0:
            self.state = "skipped"
            self.message = _(
                "Skipped product %s because Shopify stock equals Odoo stock."
            ) % product.display_name
            return

        if not auto_apply_inventory_adjustment:
            self.state = "pending"
            self.message = _(
                "Inventory adjustment not applied (auto apply disabled). Difference: %s"
            ) % self.difference_qty
            return

        try:
            Quant = self.env["stock.quant"].sudo()
            location = store.default_unshipped_order_warehouse_id.lot_stock_id or self.env.ref(
                "stock.stock_location_stock"
            )
            company = store.company_id

            quant = Quant.search(
                [
                    ("product_id", "=", product.id),
                    ("location_id", "=", location.id),
                    ("company_id", "=", company.id),
                ],
                limit=1,
            )
            if quant:
                quant.quantity = quant.quantity + self.difference_qty
            else:
                Quant.create(
                    {
                        "product_id": product.id,
                        "location_id": location.id,
                        "company_id": company.id,
                        "quantity": self.difference_qty,
                    }
                )

            self.state = "done"
            self.message = _(
                "Inventory adjusted by %(diff)s units (Shopify: %(shopify)s, Odoo: %(odoo)s)."
            ) % {
                "diff": self.difference_qty,
                "shopify": self.shopify_available_qty,
                "odoo": self.odoo_qty,
            }

            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="inventory",
                message=self.message,
                payload="",
                status="success",
            )
        except Exception as e:
            self.state = "failed"
            self.message = str(e)
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="inventory",
                message="Inventory adjustment failed for %s: %s"
                % (product.display_name, str(e)),
                payload="",
                status="failed",
            )


class ShopifyImportStockWizard(models.TransientModel):
    _name = "shopify.import.stock.wizard"
    _description = "Shopify Import Stock Wizard"

    store_id = fields.Many2one(
        "shopify.store",
        string="Store",
        required=True,
    )
    auto_apply_inventory_adjustment = fields.Boolean(
        string="Auto Apply Inventory Adjustment",
        default=True,
        help="If enabled, inventory differences will be applied automatically in Odoo.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context or {}
        if ctx.get("default_store_id") and "store_id" in fields_list:
            res["store_id"] = ctx["default_store_id"]
        return res

    def action_import_stock(self):
        self.ensure_one()
        service = self.env["shopify.service"]
        queue = service.enqueue_stock_import(self.store_id)
        service.import_stock_from_queue(
            queue,
            auto_apply_inventory_adjustment=self.auto_apply_inventory_adjustment,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Import Stock"),
                "message": _(
                    "Stock import queue %(queue)s processed. Records: %(records)s."
                )
                % {
                    "queue": queue.name,
                    "records": queue.records_to_process,
                },
                "type": "success",
                "sticky": False,
            },
        }

