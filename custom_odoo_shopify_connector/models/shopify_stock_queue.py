from odoo import _, api, fields, models

from ..services.inventory_service import InventoryService
from ..services.retry_policy import classify_exception, next_retry_at


class ShopifyStockQueue(models.Model):
    _name = "shopify.stock.queue"
    _description = "Shopify Stock Export Queue"
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
            ("manual", "Manual"),
            ("scheduler", "Scheduler"),
        ],
        string="Operation Type",
        required=True,
        default="manual",
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
    export_stock_from = fields.Datetime(
        string="Export Stock From",
        help="Only products whose last stock movement write date is after this date are included.",
    )
    line_ids = fields.One2many(
        "shopify.stock.queue.line",
        "queue_id",
        string="Stock Lines",
        copy=False,
    )
    products_to_process = fields.Integer(
        string="Products To Process",
    )
    processed_products = fields.Integer(
        string="Processed Products",
    )
    retry_count = fields.Integer(default=0, index=True)
    next_retry_at = fields.Datetime(index=True)
    last_error = fields.Text(string="Last Error")
    error_message = fields.Text(string="Error Message")

    def _lock_for_processing(self):
        """Lock one queue row so only one worker can process it."""
        self.ensure_one()
        self.env.cr.execute(
            """
            SELECT id, status
            FROM shopify_stock_queue
            WHERE id = %s AND status IN ('pending', 'processing')
            FOR UPDATE SKIP LOCKED
            """,
            (self.id,),
        )
        row = self.env.cr.fetchone()
        if not row:
            return False
        if row[1] == "pending":
            self.env.cr.execute(
                """
                UPDATE shopify_stock_queue
                SET status = 'processing'
                WHERE id = %s
                """,
                (self.id,),
            )
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shopify.stock.queue"
                ) or _("New")
        return super().create(vals_list)

    def _process_queue(self, limit=100):
        """Process pending lines in batches."""
        Inventory = InventoryService(self.env)
        VariantMap = self.env["shopify.variant.map"]
        ShopifyLocation = self.env["shopify.location"]
        for queue in self:
            if not queue._lock_for_processing():
                continue
            pending_lines = queue.line_ids.filtered(
                lambda l: l.state == "pending"
                and (not l.next_retry_at or l.next_retry_at <= fields.Datetime.now())
            )[:limit]
            for line in pending_lines:
                product = line.product_id
                store = queue.store_id

                # Resolve Shopify location configuration for this store
                # and compute stock based on mapped export warehouses.
                location_rec = ShopifyLocation.search(
                    [
                        ("shopify_store_id", "=", store.id),
                        ("shopify_location_id", "=", store.shopify_location_id),
                    ],
                    limit=1,
                )
                try:
                    line.state = "processing"
                    stock_service = self.env["shopify.stock.service"]

                    # Aggregate quantity from all mapped export warehouses if configured.
                    if location_rec and location_rec.export_stock_warehouse_ids:
                        total_qty = 0.0
                        for wh in location_rec.export_stock_warehouse_ids:
                            loc = wh.lot_stock_id
                            if not loc:
                                continue
                            total_qty += stock_service._compute_base_quantity(
                                product, store, location=loc
                            )
                        base_qty = total_qty
                    else:
                        # Fallback to global quantity computation when no warehouse mapping is set.
                        base_qty = stock_service._compute_base_quantity(product, store)

                    final_qty = stock_service._apply_product_stock_rule(product, base_qty)
                    final_qty_int = int(round(final_qty))

                    # Duplicate protection: skip if qty unchanged since last sync
                    mapping = VariantMap.search(
                        [
                            ("store_id", "=", store.id),
                            ("product_id", "=", product.id),
                        ],
                        limit=1,
                    )
                    if not mapping or not mapping.shopify_inventory_item_id:
                        line.state = "failed"
                        line.error_message = _(
                            "Missing Shopify variant mapping or inventory item id."
                        )
                        continue

                    if (
                        mapping.last_synced_qty is not False
                        and mapping.last_synced_qty == final_qty_int
                        and mapping.last_synced_at
                        and line.last_movement_date
                        and line.last_movement_date <= mapping.last_synced_at
                    ):
                        line.state = "done"
                        line.exported_quantity = final_qty_int
                        continue

                    Inventory.update_inventory_to_shopify(
                        store=store,
                        product=product,
                        quantity=final_qty_int,
                        inventory_item_id=mapping.shopify_inventory_item_id,
                        location_id=store.shopify_location_id,
                    )

                    mapping.last_synced_qty = final_qty_int
                    mapping.last_synced_at = fields.Datetime.now()

                    line.state = "done"
                    line.exported_quantity = final_qty_int
                    line.error_message = False
                    line.last_error = False
                    line.next_retry_at = False
                except Exception as e:
                    transient, _, error_message = classify_exception(e)
                    next_attempt = line.retry_count + 1
                    if transient and line.retry_count < 3:
                        line.state = "pending"
                        line.retry_count = next_attempt
                        line.next_retry_at = next_retry_at(next_attempt)
                    else:
                        line.state = "failed"
                        line.next_retry_at = False
                    line.error_message = error_message
                    line.last_error = error_message

                queue.processed_products = len(
                    queue.line_ids.filtered(lambda l: l.state in ("done", "failed"))
                )

            if all(l.state == "done" for l in queue.line_ids):
                queue.status = "done"
            elif any(l.state == "failed" for l in queue.line_ids) and not any(
                l.state in ("pending", "processing") for l in queue.line_ids
            ):
                queue.status = "failed"
            else:
                queue.status = "processing"


class ShopifyStockQueueLine(models.Model):
    _name = "shopify.stock.queue.line"
    _description = "Shopify Stock Export Queue Line"

    queue_id = fields.Many2one(
        "shopify.stock.queue",
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
    last_movement_date = fields.Datetime(
        string="Last Stock Movement",
    )
    exported_quantity = fields.Float(
        string="Exported Quantity",
    )
    state = fields.Selection(
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
    retry_count = fields.Integer(default=0, index=True)
    next_retry_at = fields.Datetime(index=True)
    last_error = fields.Text(string="Last Error")
    error_message = fields.Text(string="Error Message")

