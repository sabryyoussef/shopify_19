from odoo import _


class OrderMapper:
    """Mapper for transforming Shopify order payloads into Odoo order values."""

    def __init__(self, env):
        self.env = env

    def map_order_payload(self, payload, store=None):
        """Return (order_vals, line_vals_list) for a Shopify order payload."""
        Partner = self.env["res.partner"]
        ProductProduct = self.env["product.product"]

        shopify_order_id = payload.get("id")
        client_ref = "SHOPIFY-%s" % shopify_order_id if shopify_order_id else False

        customer = payload.get("customer") or {}
        partner = self._get_or_create_partner(customer)

        order_vals = {
            "partner_id": partner.id,
            "company_id": store.company_id.id if store and store.company_id else False,
            "client_order_ref": client_ref,
            "origin": payload.get("name"),
            "shopify_order_id": str(shopify_order_id) if shopify_order_id else False,
        }

        line_vals_list = []
        line_items = payload.get("line_items") or []
        for item in line_items:
            sku = item.get("sku")
            quantity = float(item.get("quantity") or 0.0)
            price = float(item.get("price") or 0.0)
            name = item.get("name") or _("Shopify Item")

            product = False
            if sku:
                product = ProductProduct.search([("default_code", "=", sku)], limit=1)

            if not product:
                product = ProductProduct.create(
                    {
                        "name": name,
                        "default_code": sku,
                        "lst_price": price,
                    }
                )

            line_vals_list.append(
                {
                    "product_id": product.id,
                    "name": name,
                    "product_uom_qty": quantity,
                    "price_unit": price,
                }
            )

        return order_vals, line_vals_list

    def _get_or_create_partner(self, customer):
        Partner = self.env["res.partner"]
        shopify_customer_id = str(customer.get("id") or "")
        email = customer.get("email")
        phone = customer.get("phone")
        first_name = customer.get("first_name") or ""
        last_name = customer.get("last_name") or ""
        name = (first_name + " " + last_name).strip() or email or _("Shopify Customer")

        partner = False
        if shopify_customer_id:
            partner = Partner.search(
                [("shopify_customer_id", "=", shopify_customer_id), ("parent_id", "=", False)],
                limit=1,
            )
        if not partner and email:
            partner = Partner.search([("email", "=", email), ("parent_id", "=", False)], limit=1)
        if not partner and phone:
            partner = Partner.search([("phone", "=", phone), ("parent_id", "=", False)], limit=1)
        if partner and partner.parent_id:
            partner = partner.commercial_partner_id

        address = False
        addresses = customer.get("addresses") or []
        if addresses:
            address = addresses[0]

        vals = {
            "name": name,
            "email": email,
            "phone": phone,
            "shopify_customer_id": shopify_customer_id or False,
        }

        if address:
            vals.update(
                {
                    "street": address.get("address1"),
                    "street2": address.get("address2"),
                    "city": address.get("city"),
                    "zip": address.get("zip"),
                    "country_id": self._get_country_id(address.get("country_code")),
                    "state_id": self._get_state_id(
                        address.get("province_code"),
                        address.get("country_code"),
                    ),
                }
            )

        if partner:
            partner.write(vals)
        else:
            partner = Partner.create(vals)
        return partner

    def _get_country_id(self, country_code):
        if not country_code:
            return False
        country = self.env["res.country"].search(
            [("code", "=", country_code)], limit=1
        )
        return country.id or False

    def _get_state_id(self, state_code, country_code):
        if not state_code or not country_code:
            return False
        country = self.env["res.country"].search(
            [("code", "=", country_code)], limit=1
        )
        if not country:
            return False
        state = self.env["res.country.state"].search(
            [("code", "=", state_code), ("country_id", "=", country.id)],
            limit=1,
        )
        return state.id or False

