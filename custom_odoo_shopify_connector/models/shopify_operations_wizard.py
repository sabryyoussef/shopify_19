from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ShopifyOperationsWizard(models.TransientModel):
    _name = "shopify.operations.wizard"
    _description = "Shopify Operations Wizard"

    store_id = fields.Many2one(
        "shopify.store",
        string="Store",
        required=True,
    )
    operation = fields.Selection(
        [
            ("import_products", "Import Products"),
            ("import_customers", "Import Customers"),
            ("import_orders", "Import Orders"),
            ("import_shipped_orders", "Import Shipped Orders"),
            ("import_inventory", "Import Inventory"),
            ("import_locations", "Import Locations"),
            ("upload_products", "Upload Products"),
            ("specific_product", "Specific Product (by ID)"),
            ("specific_customer", "Specific Customer (by ID)"),
        ],
        string="Operation",
        required=True,
        default="import_products",
    )
    shopify_product_id = fields.Char(
        string="Shopify Product ID",
        help="e.g. 7821491896406",
    )
    shopify_customer_id = fields.Char(
        string="Shopify Customer ID",
    )
    file_data = fields.Binary(
        string="Excel File",
        attachment=False,
    )
    file_filename = fields.Char(string="Filename")
    import_based_on = fields.Selection(
        [
            ("create_date", "Create Date"),
            ("update_date", "Update Date"),
        ],
        string="Import Based On",
        default="create_date",
    )
    from_date = fields.Datetime(string="From Date")
    to_date = fields.Datetime(string="To Date")
    start_date = fields.Datetime(string="Start Date")
    end_date = fields.Datetime(string="End Date")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get("default_store_id") or self.env.context.get(
            "active_id"
        )
        if active_id and "store_id" in fields_list:
            res["store_id"] = active_id
        return res

    def action_execute(self):
        self.ensure_one()
        operation = self._normalized_operation()

        if operation == "import_products":
            return self._execute_import_products()
        if operation == "upload_products":
            return self._execute_upload_products()
        if operation == "import_customers":
            return self._execute_import_customers()
        if operation == "specific_product":
            return self._execute_specific_product()
        if operation == "specific_customer":
            return self._execute_specific_customer()
        if operation == "import_orders":
            return self._execute_import_orders()
        if operation == "import_shipped_orders":
            return self._execute_import_shipped_orders()
        if operation == "import_inventory":
            return self._execute_import_inventory()
        if operation == "import_locations":
            return self._execute_import_locations()

        raise UserError(_("Unsupported operation: %s") % self.operation)

    def _normalized_operation(self):
        legacy_map = {
            "spacific_products": "specific_product",
            "spacific_customer": "specific_customer",
        }
        return legacy_map.get(self.operation, self.operation)

    def _execute_import_products(self):
        from ..services.queue_service import ProductQueueService

        service = ProductQueueService(self.env)
        queue = service.enqueue_products_by_date(
            store=self.store_id,
            import_based_on=self.import_based_on or "create_date",
            from_date=self.from_date,
            to_date=self.to_date,
        )
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_product_queue"
        )
        action["res_id"] = queue.id
        action["view_mode"] = "form"
        return action

    def _execute_upload_products(self):
        if not self.file_data:
            raise UserError(_("Please upload an Excel file (.xlsx)."))
        from ..services.excel_product_import import import_products_from_excel

        try:
            created, updated, errors = import_products_from_excel(
                self.env, self.file_data, self.file_filename
            )
        except ValueError as e:
            raise UserError(str(e))
        msg = _("Products created: %s, updated: %s.") % (created, updated)
        if errors:
            msg += "\n" + _("Errors:") + "\n" + "\n".join(errors[:20])
            if len(errors) > 20:
                msg += "\n" + _("... and %s more.") % (len(errors) - 20)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Upload Products"),
                "message": msg,
                "type": "success" if not errors else "warning",
                "sticky": bool(errors),
            },
        }

    def _execute_import_customers(self):
        """Create, process, and open the customer import queue."""
        service = self.env["shopify.service"]
        queue = service.enqueue_customer_import(self.store_id)
        # Process immediately so the opened queue shows live status/counts.
        service.import_customers_from_queue(queue)
        try:
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "custom_odoo_shopify_connector.action_shopify_customer_import_queue"
            )
        except ValueError:
            action = {
                "type": "ir.actions.act_window",
                "name": _("Customer Import Queue"),
                "res_model": "shopify.customer.import.queue",
                "view_mode": "list,form",
            }
        action["res_id"] = queue.id
        action["view_mode"] = "form"
        action["views"] = [(False, "form")]
        action["target"] = "current"
        action["context"] = {
            "default_store_id": self.store_id.id,
        }
        return action

    def _execute_specific_product(self):
        product_id = (self.shopify_product_id or "").strip()
        if not product_id:
            raise UserError(_("Please enter a Shopify Product ID (e.g. 7821491896406)."))
        api_client = self.store_id._get_api_client()
        try:
            response = api_client.get_product_by_id(product_id)
        except Exception as e:
            raise UserError(_("Failed to fetch product from Shopify: %s") % e)
        product_payload = response.get("product") or response
        if not product_payload or not product_payload.get("id"):
            raise UserError(_("Product not found in Shopify."))
        from ..services.product_service import ProductService
        from ..services.queue_service import ProductQueueService

        ProductService(self.env).import_shopify_product(product_payload, self.store_id)

        # Add to product queue and show it
        queue_service = ProductQueueService(self.env)
        Queue = self.env["shopify.product.queue"]
        Line = self.env["shopify.product.queue.line"]
        queue = Queue.create({"store_id": self.store_id.id})
        queue_service._create_queue_line(Line, queue.id, product_payload)
        line = queue.line_ids[0]
        line.write({"state": "done", "message": _("Imported via Specific Product")})

        action = self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_product_queue"
        )
        action["res_id"] = queue.id
        action["view_mode"] = "form"
        return action

    def _execute_specific_customer(self):
        customer_id = (self.shopify_customer_id or "").strip()
        if not customer_id:
            raise UserError(_("Please enter a Shopify Customer ID."))
        self.env["shopify.customer.sync"].import_customer_by_id(
            self.store_id, customer_id
        )
        return {"type": "ir.actions.act_window_close"}

    def _execute_import_orders(self):
        import json
        from ..services.queue_service import _shopify_datetime
        from ..services.order_import_service import OrderImportService

        store = self.store_id
        api_client = store._get_api_client()

        params = {}
        if self.from_date:
            params["created_at_min"] = _shopify_datetime(self.from_date)
        if self.to_date:
            params["created_at_max"] = _shopify_datetime(self.to_date)

        orders = api_client.get_orders(**params)
        Queue = self.env["shopify.order.queue"]
        import_service = OrderImportService(self.env)
        created = 0

        for order in orders:
            if not import_service.should_import_order(store, order):
                continue
            Queue.create(
                {
                    "store_id": store.id,
                    "shopify_order_id": str(order.get("id") or ""),
                    "payload": json.dumps(order),
                    "state": "pending",
                    "job_type": "order",
                }
            )
            created += 1

        # Redirect the user to the queue monitor for visibility/debugging.
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_order_queue"
        )
        action["context"] = dict(
            self.env.context,
            search_default_store_id=self.store_id.id,
            search_default_pending=1,
            search_default_failed=0,
        )
        # Prefer list view; no specific record id.
        action["view_mode"] = "list,form"
        return action

    def _execute_import_shipped_orders(self):
        self.ensure_one()
        store = self.store_id
        start = self.start_date or self.from_date
        end = self.end_date or self.to_date
        if not start or not end:
            raise UserError(_("Please set Start Date and End Date."))
        if start > end:
            raise UserError(_("Start Date must be before End Date."))

        queue = self.env["shopify.service"].enqueue_import_shipped_orders(store, start, end)

        action = self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_import_queue"
        )
        action["res_id"] = queue.id
        action["view_mode"] = "form"
        return action

    def _execute_import_inventory(self):
        """Open the stock import wizard."""
        try:
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "custom_odoo_shopify_connector.action_shopify_import_stock_wizard"
            )
        except ValueError:
            # Fallback for environments where the XML action is not loaded yet.
            action = {
                "type": "ir.actions.act_window",
                "name": _("Import Stock"),
                "res_model": "shopify.import.stock.wizard",
                "view_mode": "form",
                "target": "new",
            }
        action["context"] = {
            "default_store_id": self.store_id.id,
        }
        return action

    def _execute_import_locations(self):
        from ..services.location_service import LocationService

        service = LocationService(self.env)
        created, updated = service.import_locations(self.store_id)
        message = _(
            "Location import completed: %(created)s created, %(updated)s updated."
        ) % {"created": created, "updated": updated}
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Import Locations"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

