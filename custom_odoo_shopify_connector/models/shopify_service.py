import json
import time

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..services.queue_service import _shopify_datetime
from ..services.retry_policy import classify_exception, next_retry_at


class ShopifyService(models.AbstractModel):
    _name = "shopify.service"
    _description = "Shopify Service"

    @api.model
    def _log(
        self,
        store,
        message,
        payload=None,
        status="success",
        order_id=None,
        operation="order",
        response=None,
        queue_id=None,
        shopify_id=None,
        attempt=None,
        duration_ms=None,
        error_type=None,
    ):
        return self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type=operation,
            message=message,
            payload=payload or "",
            status=status,
            response=response,
            order_id=order_id,
            queue_id=queue_id,
            shopify_id=shopify_id,
            attempt=attempt,
            duration_ms=duration_ms,
            error_type=error_type,
        )

    @api.model
    def enqueue_import_shipped_orders(self, store, start_date, end_date):
        Queue = self.env["shopify.import.queue"]
        queue = Queue.create(
            {
                "store_id": store.id,
                "operation_type": "import_shipped_orders",
                "status": "pending",
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        self._log(
            store,
            _("Shipped orders import queued: %(start)s → %(end)s")
            % {"start": fields.Datetime.to_string(start_date), "end": fields.Datetime.to_string(end_date)},
            payload=json.dumps({"queue_id": queue.id}),
            status="success",
            operation="order",
            queue_id=queue.id,
        )
        return queue

    @api.model
    def enqueue_stock_import(self, store):
        """Create a stock import queue for the given store."""
        Queue = self.env["shopify.stock.import.queue"]
        queue = Queue.create(
            {
                "store_id": store.id,
                "operation_type": "import_stock",
                "status": "pending",
            }
        )
        self._log(
            store,
            _("Stock import queued."),
            payload=json.dumps({"queue_id": queue.id}),
            status="success",
            operation="inventory",
            queue_id=queue.id,
        )
        return queue

    @api.model
    def enqueue_customer_import(self, store):
        """Create a customer import queue for the given store."""
        Queue = self.env["shopify.customer.import.queue"]
        queue = Queue.create(
            {
                "store_id": store.id,
                "operation_type": "import_customers",
                "status": "pending",
            }
        )
        self._log(
            store,
            _("Customer import queued."),
            payload=json.dumps({"queue_id": queue.id}),
            status="success",
            operation="customer",
            queue_id=queue.id,
        )
        return queue

    def _claim_customer_queue_for_processing(self, queue):
        queue.ensure_one()
        if queue.status == "pending":
            self.env.cr.execute(
                """
                UPDATE shopify_customer_import_queue
                SET status = 'processing'
                WHERE id = %s AND status = 'pending'
                RETURNING id
                """,
                (queue.id,),
            )
            return bool(self.env.cr.fetchone())
        return queue.status == "processing"

    def _get_shopify_api_client(self, store):
        return store._get_api_client()

    def _map_shopify_address_vals(self, addr):
        if not addr:
            return {}
        country_code = addr.get("country_code") or addr.get("country_code_v2")
        country_name = addr.get("country")
        province_code = addr.get("province_code")
        province_name = addr.get("province")
        country = self.env["res.country"].search([("code", "=", country_code)], limit=1)
        if not country and country_name:
            country = self.env["res.country"].search([("name", "=", country_name)], limit=1)
        state = False
        if country and province_code:
            state = self.env["res.country.state"].search(
                [("code", "=", province_code), ("country_id", "=", country.id)],
                limit=1,
            )
        if country and not state and province_name:
            state = self.env["res.country.state"].search(
                [("name", "=", province_name), ("country_id", "=", country.id)],
                limit=1,
            )
        vals = {}
        if addr.get("address1"):
            vals["street"] = addr.get("address1")
        if addr.get("address2"):
            vals["street2"] = addr.get("address2")
        if addr.get("city"):
            vals["city"] = addr.get("city")
        if addr.get("zip"):
            vals["zip"] = addr.get("zip")
        if country:
            vals["country_id"] = country.id
        if state:
            vals["state_id"] = state.id
        if addr.get("company"):
            vals["company_name"] = addr.get("company")
        return vals

    def _upsert_customer_partner(self, Partner, customer_payload):
        shopify_id = str(customer_payload.get("id") or "")
        email = (customer_payload.get("email") or "").strip()
        first_name = customer_payload.get("first_name") or ""
        last_name = customer_payload.get("last_name") or ""
        phone = customer_payload.get("phone") or False
        name = (first_name + " " + last_name).strip() or email or _("Shopify Customer")

        # Duplicate protection: by shopify_customer_id, then email
        partner = False
        if shopify_id:
            partner = Partner.search([("shopify_customer_id", "=", shopify_id)], limit=1)
        if not partner and email:
            partner = Partner.search([("email", "=", email)], limit=1)

        addresses = customer_payload.get("addresses") or []
        default_address = customer_payload.get("default_address") or (addresses[0] if addresses else None)
        if not phone and default_address and default_address.get("phone"):
            phone = default_address.get("phone")

        base_vals = {
            "name": name,
            "shopify_customer_id": shopify_id,
            "customer_rank": 1,
        }
        if email:
            base_vals["email"] = email
        if phone:
            base_vals["phone"] = phone
        billing_vals = self._map_shopify_address_vals(default_address)

        if partner:
            partner.write(base_vals | billing_vals)
        else:
            partner = Partner.create(base_vals | billing_vals)

        return partner, addresses, default_address

    def _upsert_customer_delivery_contact(self, Partner, partner, addresses, default_address):
        shipping_addr = None
        for addr in addresses or []:
            if addr.get("default"):
                continue
            if default_address and addr.get("id") == default_address.get("id"):
                continue
            shipping_addr = addr
            break
        if not shipping_addr:
            return
        shipping_vals = self._map_shopify_address_vals(shipping_addr)
        if not shipping_vals:
            return
        # Avoid creating a child delivery contact that duplicates the same
        # billing fields already present on the parent contact.
        billing_fields = ["street", "street2", "city", "zip", "country_id", "state_id"]
        same_as_parent = True
        for field_name in billing_fields:
            incoming_value = shipping_vals.get(field_name)
            current_value = partner[field_name]
            if field_name in ("country_id", "state_id"):
                current_value = current_value.id if current_value else False
            if incoming_value not in (False, None, "") and incoming_value != current_value:
                same_as_parent = False
                break
        if same_as_parent:
            return
        shipping_vals.update(
            {
                "parent_id": partner.id,
                "type": "delivery",
                "name": partner.name,
            }
        )
        existing_delivery = Partner.search(
            [("parent_id", "=", partner.id), ("type", "=", "delivery")],
            limit=1,
        )
        if existing_delivery:
            existing_delivery.write(shipping_vals)
        else:
            Partner.create(shipping_vals)

    @api.model
    def import_customers_from_queue(self, queue, batch_size=250):
        """Fetch customers from Shopify and create/update partners via the queue."""
        queue.ensure_one()
        if not self._claim_customer_queue_for_processing(queue):
            return

        store = queue.store_id
        max_retries = 3
        api_client = self._get_shopify_api_client(store)

        started_at = time.perf_counter()
        try:
            customers = api_client.get_customers(limit=batch_size)
        except Exception as e:
            transient, status_code, error_message = classify_exception(e)
            next_attempt = queue.retry_count + 1
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            values = {
                "error_message": error_message,
                "last_error": error_message,
            }
            if transient and queue.retry_count < max_retries:
                values.update(
                    {
                        "status": "pending",
                        "retry_count": next_attempt,
                        "next_retry_at": next_retry_at(next_attempt),
                    }
                )
            else:
                values.update({"status": "failed", "next_retry_at": False})
            queue.write(values)
            self._log(
                store,
                _("Failed to fetch customers from Shopify: %s") % error_message,
                payload="",
                status="failed",
                operation="customer",
                queue_id=queue.id,
                attempt=next_attempt,
                duration_ms=elapsed_ms,
                error_type=("http_%s" % status_code) if status_code else ("transient" if transient else "unknown"),
            )
            return

        queue.last_error = False
        queue.error_message = False
        queue.records_to_process = len(customers)

        Partner = self.env["res.partner"].sudo()
        processed = 0

        for c in customers:
            processed += 1
            partner, addresses, default_address = self._upsert_customer_partner(Partner, c)
            self._upsert_customer_delivery_contact(Partner, partner, addresses, default_address)

            queue.processed_records = processed

        queue.status = "done"
        queue.retry_count = 0
        queue.next_retry_at = False

        self._log(
            store,
            _("Customers import completed. Records: %s") % queue.records_to_process,
            payload="",
            status="success",
            operation="customer",
            queue_id=queue.id,
        )

    @api.model
    def import_stock_from_queue(self, queue, auto_apply_inventory_adjustment=True):
        """Fetch inventory levels from Shopify and apply to Odoo via the queue."""
        queue.ensure_one()
        store = queue.store_id
        api_client = store._get_api_client()
        VariantMap = self.env["shopify.variant.map"]
        Product = self.env["product.product"]

        params = {}
        if store.shopify_location_id:
            params["location_ids"] = store.shopify_location_id

        started_at = time.perf_counter()
        try:
            inventory_levels = api_client.get_inventory(**params)
        except Exception as e:
            transient, status_code, error_message = classify_exception(e)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            self._log(
                store,
                _("Failed to fetch inventory from Shopify: %s") % error_message,
                payload=json.dumps(params),
                status="failed",
                operation="inventory",
                queue_id=queue.id,
                attempt=(queue.retry_count or 0) + 1,
                duration_ms=elapsed_ms,
                error_type=("http_%s" % status_code) if status_code else ("transient" if transient else "unknown"),
            )
            queue.status = "failed"
            queue.error_message = error_message
            return

        queue.records_to_process = len(inventory_levels)

        # Build mapping: inventory_item_id -> variant map
        item_id_to_map = {}
        if inventory_levels:
            item_ids = list({str(lvl.get("inventory_item_id")) for lvl in inventory_levels})
            maps = VariantMap.search(
                [
                    ("store_id", "=", store.id),
                    ("shopify_inventory_item_id", "in", item_ids),
                ]
            )
            for m in maps:
                item_id_to_map[m.shopify_inventory_item_id] = m

        Line = self.env["shopify.stock.import.queue.line"]

        for lvl in inventory_levels:
            inventory_item_id = str(lvl.get("inventory_item_id") or "")
            available = float(lvl.get("available") or 0.0)

            mapping = item_id_to_map.get(inventory_item_id)
            if not mapping:
                self._log(
                    store,
                    _("No product mapping for inventory item %s; skipped.")
                    % inventory_item_id,
                    payload=json.dumps(lvl),
                    status="failed",
                    operation="inventory",
                    queue_id=queue.id,
                    shopify_id=inventory_item_id,
                    error_type="mapping_missing",
                )
                continue

            product = mapping.product_id
            # Lot/serial products will be skipped later per line
            # Compute current Odoo stock in main warehouse location
            location = store.default_unshipped_order_warehouse_id.lot_stock_id or self.env.ref(
                "stock.stock_location_stock"
            )

            Quant = self.env["stock.quant"].sudo()
            quant = Quant.search(
                [
                    ("product_id", "=", product.id),
                    ("location_id", "=", location.id),
                    ("company_id", "=", store.company_id.id),
                ],
                limit=1,
            )
            odoo_qty = quant.quantity if quant else 0.0
            difference = available - odoo_qty

            Line.create(
                {
                    "queue_id": queue.id,
                    "product_id": product.id,
                    "inventory_item_id": inventory_item_id,
                    "shopify_location_id": str(lvl.get("location_id") or ""),
                    "shopify_available_qty": available,
                    "odoo_qty": odoo_qty,
                    "difference_qty": difference,
                    "state": "pending",
                }
            )

        # Process the queue lines
        queue._process_queue(auto_apply_inventory_adjustment=auto_apply_inventory_adjustment)

        self._log(
            store,
            _("Stock import completed. Records: %s") % queue.records_to_process,
            payload=json.dumps({"queue_id": queue.id}),
            status="success",
            operation="inventory",
            queue_id=queue.id,
        )

    @api.model
    def import_shipped_orders_from_queue(self, queue):
        """Fetch shipped/fulfilled orders from Shopify and import them into Odoo.

        - financial_status: paid OR refunded
        - fulfillment_status: fulfilled
        - duplicate protection via sale.order.shopify_order_id
        - creates sale.order + invoices
        - does NOT create delivery orders; creates inventory adjustment moves instead
        """
        queue.ensure_one()
        store = queue.store_id
        api_client = store._get_api_client()

        params = {
            "status": "any",
            "fulfillment_status": "fulfilled",
            "created_at_min": _shopify_datetime(queue.start_date),
            "created_at_max": _shopify_datetime(queue.end_date),
            "limit": 250,
        }

        self._log(
            store,
            _("Shopify API request: fetch fulfilled orders"),
            payload=json.dumps(params),
            operation="order",
            queue_id=queue.id,
        )
        started_at = time.perf_counter()
        orders = api_client.get_orders(**params) or []
        fetch_duration_ms = int((time.perf_counter() - started_at) * 1000)
        queue.records_to_process = len(orders)

        SaleOrder = self.env["sale.order"].sudo()
        imported = 0
        processed = 0

        for order in orders:
            processed += 1
            shopify_order_id = str(order.get("id") or "")
            queue.processed_records = processed

            if not shopify_order_id:
                continue

            # Filter: paid + fulfilled OR refunded + fulfilled
            financial_status = (order.get("financial_status") or "").lower()
            fulfillment_status = (order.get("fulfillment_status") or "").lower()
            if fulfillment_status != "fulfilled":
                continue
            if financial_status not in ("paid", "refunded", "partially_refunded"):
                continue

            # Duplicate protection
            if SaleOrder.search_count([("shopify_order_id", "=", shopify_order_id)]):
                self._log(
                    store,
                    _("Skipped duplicate Shopify order %s") % shopify_order_id,
                    status="success",
                    order_id=shopify_order_id,
                    operation="order",
                    queue_id=queue.id,
                    shopify_id=shopify_order_id,
                )
                continue

            try:
                with self.env.cr.savepoint():
                    self._import_single_fulfilled_order(store, order)
                imported += 1
            except Exception as e:
                self._log(
                    store,
                    _("Failed to import fulfilled order %s: %s") % (shopify_order_id, str(e)),
                    payload=json.dumps(order)[:5000],
                    status="failed",
                    order_id=shopify_order_id,
                    operation="order",
                    queue_id=queue.id,
                    shopify_id=shopify_order_id,
                    error_type="import_failed",
                )

        self._log(
            store,
            _("Fulfilled orders import completed. Imported: %s") % imported,
            payload=json.dumps({"queue_id": queue.id, "imported": imported, "total": len(orders)}),
            status="success",
            operation="order",
            queue_id=queue.id,
            duration_ms=fetch_duration_ms,
        )
        # Advance shipped-orders checkpoint only after successful queue completion.
        store.write({"last_shipped_orders_import_time": queue.end_date})

    @api.model
    def _import_single_fulfilled_order(self, store, payload):
        """Create sale.order + invoices and inventory adjustment moves."""
        from ..services.order_service import OrderService
        from ..services.order_import_service import OrderImportService

        shopify_order_id = str(payload.get("id") or "")
        if not shopify_order_id:
            raise UserError(_("Missing Shopify order id in payload."))

        import_service = OrderImportService(self.env)
        order_service = OrderService(self.env, import_service=import_service)

        sale_order = order_service.create_order_from_payload(payload, store)

        # Mark as fulfilled import
        try:
            sale_order.shopify_fulfillment_status = "fulfilled"
        except Exception:
            pass

        # Avoid delivery orders: cancel and remove any generated pickings (if any)
        for picking in sale_order.picking_ids:
            if picking.state not in ("done", "cancel"):
                picking.action_cancel()
            if picking.state == "cancel":
                try:
                    picking.unlink()
                except Exception:
                    pass

        # Invoices: create & post
        invoices = sale_order._create_invoices()
        if invoices:
            invoices.action_post()

        financial_status = (payload.get("financial_status") or "").lower()
        if financial_status in ("refunded", "partially_refunded"):
            # Create refund (credit note) for posted invoices
            for inv in invoices:
                refund = inv._reverse_moves(default_values_list=[{"ref": _("Refund for Shopify order %s") % shopify_order_id}])
                if refund:
                    refund.action_post()

        # Inventory adjustment via stock moves (Stock -> Inventory Loss)
        self._create_inventory_adjustment_moves(store, sale_order, payload)

        shop_user = store._resolve_import_order_salesperson_user()
        if shop_user:
            sale_order.sudo().write({"user_id": shop_user.id})
            if sale_order.invoice_ids:
                sale_order.invoice_ids.sudo().write({"invoice_user_id": shop_user.id})

        self._log(
            store,
            _("Imported fulfilled Shopify order %s") % shopify_order_id,
            payload=json.dumps({"odoo_order": sale_order.name}),
            status="success",
            order_id=shopify_order_id,
            operation="order",
            shopify_id=shopify_order_id,
        )
        return sale_order

    @api.model
    def _create_inventory_adjustment_moves(self, store, sale_order, payload):
        """Create stock.move lines to reduce inventory because goods were delivered outside Odoo."""
        StockMove = self.env["stock.move"].sudo()
        StockMoveLine = self.env["stock.move.line"].sudo()

        warehouse = store.default_unshipped_order_warehouse_id or self.env["stock.warehouse"].search(
            [("company_id", "=", store.company_id.id)], limit=1
        )
        stock_loc = warehouse.lot_stock_id if warehouse else self.env.ref("stock.stock_location_stock")
        inv_loc = self.env.ref("stock.stock_location_inventory")

        origin = "%s (Shopify %s)" % (sale_order.name, sale_order.shopify_order_id or "")
        lines = payload.get("line_items") or []

        for line in lines:
            qty = float(line.get("quantity") or 0.0)
            if qty <= 0:
                continue

            sku = (line.get("sku") or "").strip()
            product = False
            if sku:
                product = self.env["product.product"].sudo().search([("default_code", "=", sku)], limit=1)
            if not product:
                # fallback by name
                name = (line.get("name") or "").strip()
                product = self.env["product.product"].sudo().search([("name", "=", name)], limit=1)
            if not product:
                continue

            move = StockMove.create(
                {
                    "name": origin,
                    "product_id": product.id,
                    "product_uom": product.uom_id.id,
                    "product_uom_qty": qty,
                    "location_id": stock_loc.id,
                    "location_dest_id": inv_loc.id,
                    "origin": origin,
                    "company_id": store.company_id.id,
                }
            )
            move._action_confirm()
            move._action_assign()

            StockMoveLine.create(
                {
                    "move_id": move.id,
                    "product_id": product.id,
                    "product_uom_id": product.uom_id.id,
                    "qty_done": qty,
                    "location_id": stock_loc.id,
                    "location_dest_id": inv_loc.id,
                    "company_id": store.company_id.id,
                }
            )
            move._action_done()

    @api.model
    def cancel_order_in_shopify(
        self,
        store,
        shopify_order_id,
        reason="customer",
        message=None,
        email_customer=True,
        sale_order=None,
    ):
        """Cancel Shopify order using the Shopify Order Cancel API."""
        if not store or not shopify_order_id:
            raise UserError(_("Missing Shopify store or Shopify Order ID."))

        if sale_order and sale_order.shopify_cancelled:
            return True

        api_client = store._get_api_client()
        payload = {"reason": reason or "other", "email": bool(email_customer)}

        self._log(
            store,
            _("Shopify API request: cancel order %s") % shopify_order_id,
            payload=json.dumps(payload),
            status="success",
            order_id=shopify_order_id,
            operation="order",
        )

        started_at = time.perf_counter()
        try:
            res = api_client._request(
                "POST",
                "/orders/%s/cancel.json" % shopify_order_id,
                data=payload,
                max_retries=3,
            )
        except Exception as e:
            msg = str(e)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            transient, status_code, _ = classify_exception(e)
            # Duplicate protection: Shopify may respond "already cancelled"
            if "already cancelled" in msg.lower() or "already canceled" in msg.lower():
                res = {"skipped": True, "message": msg}
            else:
                self._log(
                    store,
                    _("Shopify cancel failed for order %s: %s") % (shopify_order_id, msg),
                    payload=json.dumps(payload),
                    status="failed",
                    order_id=shopify_order_id,
                    operation="order",
                    response=msg,
                    shopify_id=shopify_order_id,
                    duration_ms=elapsed_ms,
                    error_type=("http_%s" % status_code) if status_code else ("transient" if transient else "unknown"),
                )
                raise

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        self._log(
            store,
            _("Shopify order %s cancelled successfully.") % shopify_order_id,
            payload=json.dumps(payload),
            status="success",
            order_id=shopify_order_id,
            operation="order",
            response=json.dumps(res)[:5000] if res else "",
            shopify_id=shopify_order_id,
            duration_ms=elapsed_ms,
        )

        if sale_order:
            vals = {
                "shopify_cancelled": True,
                "shopify_cancel_reason": reason or "other",
            }
            sale_order.sudo().write(vals)
            if sale_order.state != "cancel":
                try:
                    sale_order.sudo().action_cancel()
                except Exception:
                    sale_order.sudo().write({"state": "cancel"})
        return True

    @api.model
    def refund_order_in_shopify(self, store, shopify_order_id, payload):
        """Create a refund in Shopify using the Refunds API."""
        if not store or not shopify_order_id:
            raise UserError(_("Missing Shopify store or Shopify Order ID."))

        api_client = store._get_api_client()
        started_at = time.perf_counter()
        try:
            res = api_client._request(
                "POST",
                "/orders/%s/refunds.json" % shopify_order_id,
                data=payload,
                max_retries=3,
            )
        except Exception as e:
            msg = str(e)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            transient, status_code, _ = classify_exception(e)
            # Duplicate protection: Shopify may indicate refund already exists / cannot be refunded
            if "already refunded" in msg.lower() or "cannot refund" in msg.lower():
                return {"skipped": True, "message": msg}
            self._log(
                store,
                _("Shopify refund failed for order %s: %s") % (shopify_order_id, msg),
                payload=json.dumps(payload),
                status="failed",
                order_id=shopify_order_id,
                operation="order",
                response=msg,
                shopify_id=shopify_order_id,
                duration_ms=elapsed_ms,
                error_type=("http_%s" % status_code) if status_code else ("transient" if transient else "unknown"),
            )
            raise

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        self._log(
            store,
            _("Shopify refund created for order %s") % shopify_order_id,
            payload=json.dumps(payload),
            status="success",
            order_id=shopify_order_id,
            operation="order",
            response=json.dumps(res)[:5000] if res else "",
            shopify_id=shopify_order_id,
            duration_ms=elapsed_ms,
        )
        return res

