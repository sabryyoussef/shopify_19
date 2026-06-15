from odoo import models

from ..services.inventory_service import InventoryService


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def write(self, vals):
        """Propagate inventory changes to Shopify in real time."""
        # Capture previous quantities
        if "quantity" not in vals and "inventory_quantity" not in vals:
            return super().write(vals)

        previous_quantities = {quant.id: quant.quantity for quant in self}
        res = super().write(vals)

        inventory_service = InventoryService(self.env)
        VariantMap = self.env["shopify.variant.map"]

        for quant in self:
            old_qty = previous_quantities.get(quant.id)
            new_qty = quant.quantity
            if old_qty == new_qty:
                continue

            mappings = VariantMap.search(
                [("product_id", "=", quant.product_id.id)]
            )
            for mapping in mappings:
                store = mapping.store_id
                inventory_item_id = mapping.shopify_inventory_item_id
                if not (store and inventory_item_id and quant.location_id):
                    continue

                location_id = store.shopify_location_id or False
                if not location_id:
                    continue

                inventory_service.update_inventory_to_shopify(
                    store=store,
                    product=quant.product_id,
                    quantity=new_qty,
                    inventory_item_id=inventory_item_id,
                    location_id=location_id,
                )

        return res

