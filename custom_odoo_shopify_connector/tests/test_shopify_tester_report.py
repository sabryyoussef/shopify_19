import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shopify_tester import render_detailed_report


class TestShopifyTesterReport(unittest.TestCase):
    def test_render_detailed_report_contains_product_and_metafields(self):
        product = {
            "id": 123,
            "title": "Aurora Anti-Frizz Shampoo 250ml",
            "vendor": "my store",
            "variants": [{"sku": "AUR-AF-SHAM-250", "price": "28.90"}],
        }
        metafields = [
            {
                "id": 1,
                "namespace": "odoo_custom",
                "key": "brand",
                "type": "single_line_text_field",
                "value": "Aurora",
            }
        ]

        report = render_detailed_report(product, metafields)

        self.assertIn("=== PRODUCT DETAILS (FULL JSON) ===", report)
        self.assertIn("\"title\": \"Aurora Anti-Frizz Shampoo 250ml\"", report)
        self.assertIn("=== METAFIELD 1 DETAILS ===", report)
        self.assertIn("\"namespace\": \"odoo_custom\"", report)
        self.assertIn("\"key\": \"brand\"", report)


if __name__ == "__main__":
    unittest.main()
