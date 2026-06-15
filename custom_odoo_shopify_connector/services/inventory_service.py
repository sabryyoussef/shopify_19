from odoo import _


class InventoryService:
    def __init__(self, env):
        self.env = env

    def update_inventory_to_shopify(self, store, product, quantity, inventory_item_id, location_id):
        api_client = store._get_api_client()
        try:
            api_client.update_inventory_level(
                inventory_item_id=inventory_item_id,
                available=quantity,
                location_id=location_id,
            )
        except Exception as e:
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="inventory",
                message=str(e),
                payload={
                    "product_id": product.id,
                    "quantity": quantity,
                    "inventory_item_id": inventory_item_id,
                    "location_id": location_id,
                },
                status="failed",
            )
            raise

        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="inventory",
            message=_("Inventory updated to Shopify for product %s") % product.display_name,
            payload={
                "product_id": product.id,
                "quantity": quantity,
                "inventory_item_id": inventory_item_id,
                "location_id": location_id,
            },
            status="success",
        )

