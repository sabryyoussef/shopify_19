from types import SimpleNamespace

from odoo.tests.common import TransactionCase

from ..mappers.product_mapper import ProductMapper


class TestProductMapperCustomFields(TransactionCase):
    def test_build_export_metafields_includes_block_images_and_timestamps(self):
        mapper = ProductMapper(self.env)
        fake_fields = {
            "write_date": True,
            "brand_id": True,
            "block1_image": True,
            "block2_image": True,
            "cross_sell_ids": True,
            "similar_product_ids": True,
        }
        fake_product = SimpleNamespace(
            _fields=fake_fields,
            write_date=SimpleNamespace(isoformat=lambda: "2026-04-28T21:00:00+00:00"),
            brand_id=SimpleNamespace(name="Aurora"),
            block1_image=b"ZmFrZV9pbWFnZV8x",
            block2_image=b"ZmFrZV9pbWFnZV8y",
            cross_sell_ids=[SimpleNamespace(id=10, name="Cross A")],
            similar_product_ids=[SimpleNamespace(id=20, name="Similar A")],
        )

        metafields = mapper.build_export_metafields(fake_product)
        keys = {mf["key"] for mf in metafields}

        self.assertIn("block1_image_b64", keys)
        self.assertIn("block2_image_b64", keys)
        self.assertIn("ts_block1_image", keys)
        self.assertIn("ts_block2_image", keys)
