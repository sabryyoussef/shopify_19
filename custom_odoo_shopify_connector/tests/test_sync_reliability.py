from odoo.tests.common import TransactionCase

from ..services.product_service import ProductService


class TestSyncReliability(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Store = self.env["shopify.store"].sudo()
        self.Partner = self.env["res.partner"].sudo()
        self.customer_sync = self.env["shopify.customer.sync"].sudo()

        self.store = self.Store.create(
            {
                "name": "Sync Test Store",
                "shop_url": "https://example.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "webhook_secret": "secret",
            }
        )

    def test_customer_import_updates_existing_by_shopify_id(self):
        existing = self.Partner.create(
            {
                "name": "Existing Customer",
                "shopify_customer_id": "111",
                "email": "old@example.com",
            }
        )

        payload = {
            "id": 111,
            "first_name": "Updated",
            "last_name": "Name",
            "email": "new@example.com",
            "addresses": [],
        }
        self.customer_sync._import_customer_payload(self.store, payload)

        partners = self.Partner.search([("shopify_customer_id", "=", "111")])
        self.assertEqual(len(partners), 1)
        self.assertEqual(partners.id, existing.id)
        self.assertEqual(partners.email, "new@example.com")

    def test_customer_import_does_not_overwrite_valid_address_with_empty_values(self):
        partner = self.Partner.create(
            {
                "name": "Address Keep",
                "email": "keep@example.com",
                "street": "Old Street",
                "city": "Old City",
                "zip": "12345",
            }
        )

        payload = {
            "id": 222,
            "email": "keep@example.com",
            "addresses": [
                {
                    "address1": "",
                    "address2": None,
                    "city": "",
                    "zip": None,
                    "country_code": "",
                }
            ],
        }
        self.customer_sync._import_customer_payload(self.store, payload)
        partner.invalidate_cache(["street", "city", "zip"])

        self.assertEqual(partner.street, "Old Street")
        self.assertEqual(partner.city, "Old City")
        self.assertEqual(partner.zip, "12345")

    def test_product_import_reuses_existing_template_by_sku(self):
        template = self.env["product.template"].create(
            {
                "name": "Original Product",
                "type": "consu",
            }
        )
        variant = template.product_variant_id
        variant.default_code = "SYNC-SKU-1"

        payload = {
            "id": 987654,
            "title": "Renamed On Shopify",
            "variants": [
                {
                    "id": 4444,
                    "sku": "SYNC-SKU-1",
                    "price": "19.90",
                    "weight": 0.5,
                    "inventory_item_id": 9001,
                }
            ],
            "options": [],
            "images": [],
            "tags": "",
            "product_type": "",
        }

        service = ProductService(self.env)
        service.import_shopify_product(payload, self.store)

        matches = self.env["product.template"].search([("id", "=", template.id)])
        self.assertEqual(len(matches), 1)
        product_map = self.env["shopify.product.map"].search(
            [
                ("store_id", "=", self.store.id),
                ("shopify_product_id", "=", "987654"),
            ],
            limit=1,
        )
        self.assertTrue(product_map)
        self.assertEqual(product_map.product_tmpl_id.id, template.id)

    def test_customer_import_maps_country_by_name_company_and_phone(self):
        service = self.env["shopify.service"].sudo()
        payload = {
            "id": 333,
            "first_name": "New",
            "last_name": "Customer",
            "email": "new.customer@example.com",
            "phone": "",
            "default_address": {
                "address1": "Street 1",
                "city": "Islamabad",
                "zip": "5555",
                "country": "Pakistan",
                "company": "Egel Softwares",
                "phone": "+92 315 1945928",
            },
            "addresses": [],
        }

        partner, _addresses, _default = service._upsert_customer_partner(self.Partner, payload)

        self.assertEqual(partner.phone, "+92 315 1945928")
        self.assertEqual(partner.company_name, "Egel Softwares")
        self.assertEqual(partner.street, "Street 1")
        self.assertEqual(partner.city, "Islamabad")
        self.assertEqual(partner.zip, "5555")
        self.assertTrue(partner.country_id)
        self.assertEqual(partner.country_id.name, "Pakistan")
