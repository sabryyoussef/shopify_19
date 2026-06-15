from odoo import api, models, _
from odoo.exceptions import UserError

from .license_mixin import license_is_active_strict, trial_batch_limit


class ShopifyCustomerSync(models.AbstractModel):
    _name = "shopify.customer.sync"
    _description = "Shopify Customer Synchronization"

    @api.model
    def sync_customers(self, store):
        api_client = store._get_api_client()

        try:
            customers = api_client.get_customers()
        except Exception as e:
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="customer",
                message=str(e),
                payload=False,
                status="failed",
            )
            return

        Partner = self.env["res.partner"]

        if not license_is_active_strict(self.env):
            customers = (customers or [])[: trial_batch_limit()]
            __import__("time").sleep(1)
        for c in customers:
            self._import_customer_payload(store, c)

        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="customer",
            message=_("Customers synchronized from Shopify."),
            payload=False,
            status="success",
        )

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

    def _map_address_vals(self, address):
        if not address:
            return {}
        country_code = address.get("country_code")
        country_name = address.get("country")
        province_code = address.get("province_code")
        province_name = address.get("province")

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
        if address.get("address1"):
            vals["street"] = address.get("address1")
        if address.get("address2"):
            vals["street2"] = address.get("address2")
        if address.get("city"):
            vals["city"] = address.get("city")
        if address.get("zip"):
            vals["zip"] = address.get("zip")
        if country:
            vals["country_id"] = country.id
        if state:
            vals["state_id"] = state.id
        if address.get("company"):
            vals["company_name"] = address.get("company")
        return vals

    @api.model
    def import_customer_by_id(self, store, customer_id):
        """Fetch a single customer from Shopify by ID and create/update in Odoo."""
        api_client = store._get_api_client()
        try:
            response = api_client.get_customer_by_id(customer_id)
        except Exception as e:
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="customer",
                message=str(e),
                payload=False,
                status="failed",
            )
            raise
        customer = response.get("customer") or response
        if not customer or not customer.get("id"):
            raise UserError(_("Customer not found in Shopify."))
        self._import_customer_payload(store, customer)
        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="customer",
            message=_("Customer %s imported.") % customer_id,
            payload=False,
            status="success",
        )

    def _import_customer_payload(self, store, c):
        """Create or update a single partner from Shopify customer payload."""
        Partner = self.env["res.partner"]
        shopify_customer_id = str(c.get("id") or "")
        email = c.get("email")
        first_name = c.get("first_name") or ""
        last_name = c.get("last_name") or ""
        phone = c.get("phone") or False
        name = (first_name + " " + last_name).strip() or email or _("Shopify Customer")
        partner = False
        if shopify_customer_id:
            partner = Partner.search(
                [("shopify_customer_id", "=", shopify_customer_id)],
                limit=1,
            )
        if not partner and email:
            partner = Partner.search([("email", "=", email)], limit=1)
        if not partner and phone:
            partner = Partner.search([("phone", "=", phone)], limit=1)
        address = False
        addresses = c.get("addresses") or []
        if addresses:
            address = addresses[0]
        if not phone and address and address.get("phone"):
            phone = address.get("phone")
        vals = {"name": name}
        if email:
            vals["email"] = email
        if phone:
            vals["phone"] = phone
        if shopify_customer_id:
            vals["shopify_customer_id"] = shopify_customer_id
        if address:
            vals.update(self._map_address_vals(address))
        if partner:
            partner.write(vals)
        else:
            Partner.create(vals)

    @api.model
    def cron_sync_customers(self):
        if not license_is_active_strict(self.env):
            _logger = __import__("logging").getLogger(__name__)
            _logger.warning("License inactive - cron skipped (customer sync)")
            return
        stores = self.env["shopify.store"].search([("active", "=", True)])
        for store in stores:
            self.sync_customers(store)

