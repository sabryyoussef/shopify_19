from odoo.tests.common import TransactionCase

from ..services.product_service import ProductService


class TestProductServicePriceParsing(TransactionCase):
    def test_to_float_handles_shopify_price_strings(self):
        service = ProductService(self.env)

        self.assertEqual(service._to_float("20.0"), 20.0)
        self.assertEqual(service._to_float("20.00"), 20.0)
        self.assertEqual(service._to_float("20.0 USD"), 20.0)
        self.assertEqual(service._to_float(None), 0.0)

    def test_parse_shopify_datetime_normalizes_timezone_to_naive_utc(self):
        service = ProductService(self.env)

        parsed = service._parse_shopify_datetime("2026-04-28T18:02:18-05:00")

        self.assertTrue(parsed)
        self.assertEqual(parsed.tzinfo, None)
        self.assertEqual(parsed.strftime("%Y-%m-%d %H:%M:%S"), "2026-04-28 23:02:18")

    def test_build_attribute_value_maps_uses_variant_option_values(self):
        service = ProductService(self.env)

        options = [{"name": "Size", "position": 1, "values": []}]
        variants = [
            {"option1": "250 ML", "sku": "SHAMPKEOZ100"},
            {"option1": "500 ML", "sku": "SHAMPKEOZ101"},
        ]

        attribute_map, value_map = service._build_attribute_value_maps(options, variants=variants)

        self.assertIn(1, attribute_map)
        size_attribute = attribute_map[1]
        value_names = {
            name
            for (attr_id, name), _value in value_map.items()
            if attr_id == size_attribute.id
        }
        self.assertEqual(value_names, {"250 ML", "500 ML"})

    def test_shopify_price_to_odoo_tax_excluded(self):
        service = ProductService(self.env)
        sale_tax = self.env["account.tax"].create(
            {
                "name": "VAT 20",
                "amount_type": "percent",
                "amount": 20.0,
                "type_tax_use": "sale",
                "price_include": False,
                "company_id": self.env.company.id,
            }
        )
        template = self.env["product.template"].create(
            {
                "name": "Taxed Product",
                "type": "consu",
                "taxes_id": [(6, 0, [sale_tax.id])],
            }
        )

        price_excluded = service._shopify_price_to_odoo_tax_excluded(template, "19.90")

        self.assertAlmostEqual(price_excluded, 16.5833333333, places=6)

    def test_apply_variant_price_model_uses_min_base_and_non_negative_extras(self):
        service = ProductService(self.env)
        sale_tax = self.env["account.tax"].create(
            {
                "name": "VAT 20 Variant",
                "amount_type": "percent",
                "amount": 20.0,
                "type_tax_use": "sale",
                "price_include": False,
                "company_id": self.env.company.id,
            }
        )
        template = self.env["product.template"].create(
            {
                "name": "Variant Price Product",
                "type": "consu",
                "taxes_id": [(6, 0, [sale_tax.id])],
            }
        )

        options = [{"name": "Size", "position": 1, "values": ["250 ML", "500 ML"]}]
        variants = [
            {"option1": "500 ML", "sku": "SHAMPKEOZ101", "price": "29.90"},
            {"option1": "250 ML", "sku": "SHAMPKEOZ100", "price": "19.90"},
        ]
        attribute_map, value_map = service._build_attribute_value_maps(options, variants=variants)
        service._apply_template_attribute_lines(template, attribute_map, value_map)
        variant_rows = []
        for payload in variants:
            ptav_ids = service._resolve_ptav_ids(template, payload, attribute_map, value_map)
            variant = service._upsert_variant(template, payload, ptav_ids=ptav_ids)
            if ptav_ids:
                variant.write({"product_template_attribute_value_ids": [(6, 0, ptav_ids)]})
            variant_rows.append({"payload": payload, "variant": variant})
        service._apply_variant_price_model(template, variant_rows)

        base_price = service._shopify_price_to_odoo_tax_excluded(template, "19.90")
        bigger_price = service._shopify_price_to_odoo_tax_excluded(template, "29.90")
        self.assertAlmostEqual(template.list_price, base_price, places=6)

        ptav_for_500 = self.env["product.template.attribute.value"].browse(
            service._resolve_ptav_ids(template, variants[0], attribute_map, value_map)[:1]
        )
        ptav_for_250 = self.env["product.template.attribute.value"].browse(
            service._resolve_ptav_ids(template, variants[1], attribute_map, value_map)[:1]
        )
        self.assertTrue(ptav_for_500)
        self.assertTrue(ptav_for_250)
        self.assertGreaterEqual(ptav_for_500.price_extra, 0.0)
        self.assertGreaterEqual(ptav_for_250.price_extra, 0.0)
        self.assertAlmostEqual(ptav_for_250.price_extra, 0.0, places=6)
        self.assertAlmostEqual(ptav_for_500.price_extra, bigger_price - base_price, places=6)
