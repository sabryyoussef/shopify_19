import json

from odoo import api, fields, models, _


class ShopifyStockService(models.AbstractModel):
    _name = "shopify.stock.service"
    _description = "Shopify Stock Export Service"

    @api.model
    def _log(self, store, message, product=None, status="success", response=None):
        payload = {}
        if product:
            payload["product_id"] = product.id
        return self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="inventory",
            message=message,
            payload=json.dumps(payload) if payload else "",
            status=status,
            response=response or "",
        )

    @api.model
    def _get_last_stock_movement_dates(self, products, since_date=None):
        """Return dict {product_id: last_move_write_date} for moves after since_date (if given)."""
        Move = self.env["stock.move"]
        domain = [("product_id", "in", products.ids), ("state", "=", "done")]
        if since_date:
            domain.append(("write_date", ">", since_date))
        data = Move.read_group(
            domain,
            ["product_id", "write_date:max"],
            ["product_id"],
        )
        result = {}
        for row in data:
            pid = row["product_id"][0]
            result[pid] = row["write_date_max"]
        return result

    @api.model
    def _compute_base_quantity(self, product, store, location=None):
        """Compute base stock quantity according to store configuration.

        If a specific stock.location is provided, the computation is done in the
        context of that location. This allows per-warehouse calculations when
        exporting stock for a given Shopify location.
        """
        stock_type = store.shopify_stock_type or "free_qty"

        if location:
            # Use with_context to limit quantities to the given location.
            product_ctx = product.with_context(location=location.id)
            qty_available = product_ctx.qty_available
            reserved = getattr(product_ctx, "reserved_quantity", 0.0)
            outgoing = getattr(product_ctx, "outgoing_qty", 0.0)
            incoming = getattr(product_ctx, "incoming_qty", 0.0)
        else:
            qty_available = product.qty_available
            reserved = getattr(product, "reserved_quantity", 0.0)
            outgoing = getattr(product, "outgoing_qty", 0.0)
            incoming = getattr(product, "incoming_qty", 0.0)

        if stock_type == "forecast_qty":
            return qty_available - outgoing + incoming
        # default: free to use
        return qty_available - reserved

    @api.model
    def _apply_product_stock_rule(self, product, base_qty):
        """Apply per-product fixed / percentage stock rule."""
        rule_type = product.shopify_stock_type
        value = product.shopify_stock_value or 0.0
        if not rule_type:
            return base_qty
        if rule_type == "fixed":
            return max(value, 0.0)
        if rule_type == "percentage":
            return max(base_qty * (value / 100.0), 0.0)
        return base_qty

    @api.model
    def _get_products_for_export(self, export_from, products=None):
        """Return products whose stock move write_date is after export_from.

        If products is provided, restrict the search to those products.
        """
        Move = self.env["stock.move"]
        domain = [("state", "=", "done")]
        if export_from:
            domain.append(("write_date", ">", export_from))
        if products:
            domain.append(("product_id", "in", products.ids))

        data = Move.read_group(
            domain,
            ["product_id"],
            ["product_id"],
        )
        product_ids = [row["product_id"][0] for row in data if row.get("product_id")]
        if not product_ids:
            return self.env["product.product"]
        return self.env["product.product"].browse(product_ids)

    @api.model
    def _prepare_stock_queue(self, store, products, export_from, operation_type="manual"):
        Queue = self.env["shopify.stock.queue"]
        queue = Queue.create(
            {
                "store_id": store.id,
                "operation_type": operation_type,
                "status": "pending",
                "export_stock_from": export_from,
            }
        )
        Line = self.env["shopify.stock.queue.line"]
        last_moves = self._get_last_stock_movement_dates(products, export_from)
        for product in products:
            Line.create(
                {
                    "queue_id": queue.id,
                    "product_id": product.id,
                    "last_movement_date": last_moves.get(product.id),
                    "state": "pending",
                }
            )
        queue.products_to_process = len(queue.line_ids)
        return queue

    @api.model
    def export_stock(self, store, export_from=None, products=None, operation_type="manual"):
        """High-level entry point to enqueue and process stock export."""
        if not store.shopify_location_id:
            raise ValueError(_("Shopify Location ID is not configured on the store."))

        if products:
            export_products = products
        else:
            export_products = self._get_products_for_export(export_from)

        if not export_products:
            return False

        queue = self._prepare_stock_queue(
            store=store,
            products=export_products,
            export_from=export_from,
            operation_type=operation_type,
        )
        queue._process_queue()
        return queue

