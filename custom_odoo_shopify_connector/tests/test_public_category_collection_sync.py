# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "shopify_public_category")
class TestShopifyPublicCategorySync(TransactionCase):
    """Unit tests for Migration A public-category → custom collection sync."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "product.public.category" not in cls.env:
            return
        cls.PublicCateg = cls.env["product.public.category"]
        cls.Store = cls.env["shopify.store"]
        cls.Service = cls.env["shopify.public.category.service"]
        cls.Map = cls.env["shopify.public.category.map"]

        cls.store = cls.Store.create(
            {
                "name": "Pet Spot Test",
                "shop_url": "https://ucbah1-5e.myshopify.com",
                "access_token": "test-token",
                "active": True,
            }
        )
        cls.parent = cls.PublicCateg.create({"name": "PCat Parent Food"})
        cls.child = cls.PublicCateg.create(
            {"name": "PCat Child Dry", "parent_id": cls.parent.id}
        )
        cls.empty = cls.PublicCateg.create({"name": "PCat Empty"})

    def test_stable_handle(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        self.assertEqual(
            self.Service._stable_handle(self.child),
            f"odoo-pcat-{self.child.id}",
        )

    def test_rejects_izone_store(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        bad = self.Store.create(
            {
                "name": "iZone",
                "shop_url": "https://izone-eg.myshopify.com",
                "access_token": "x",
            }
        )
        with self.assertRaises(Exception):
            self.Service._assert_petspot_store(bad)

    def test_dry_run_create_plan_no_shopify_writes(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        api = MagicMock()
        api.find_custom_collection_by_handle.return_value = None
        api.find_custom_collection_by_metafield.return_value = None
        api.list_custom_collections.return_value = []
        api.list_smart_collections.return_value = [
            {"id": 99, "title": "PCat Child Dry", "handle": "vet-pet-food"}
        ]
        api.get_custom_collection.side_effect = Exception("missing")

        with patch.object(type(self.store), "_get_api_client", return_value=api):
            result = self.Service.sync_category(self.store, self.child, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["plan"]["action"], "create")
        self.assertTrue(result["plan"]["title_conflicts"])
        api.create_custom_collection.assert_not_called()
        api.create_collect.assert_not_called()
        api.update_custom_collection.assert_not_called()

    def test_reuse_verified_handle_not_title(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        handle = f"odoo-pcat-{self.child.id}"
        api = MagicMock()
        api.find_custom_collection_by_handle.return_value = {
            "id": 555,
            "handle": handle,
            "title": "PCat Child Dry",
        }
        api.get_custom_collection_metafields.return_value = []
        api.list_custom_collections.return_value = [
            {"id": 777, "title": "PCat Child Dry", "handle": "other-handle"}
        ]
        api.list_smart_collections.return_value = []

        with patch.object(type(self.store), "_get_api_client", return_value=api):
            plan = self.Service.plan_category(self.store, self.child, api=api)

        self.assertEqual(plan["action"], "reuse")
        self.assertEqual(plan["verify_via"], "handle")
        self.assertEqual(plan["shopify_collection_id"], 555)
        # Title-only conflict listed but not adopted
        self.assertTrue(
            any(c.get("id") == 777 for c in plan["title_conflicts"])
        )

    def test_execute_creates_unpublished_and_maps(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        api = MagicMock()
        api.find_custom_collection_by_handle.return_value = None
        api.find_custom_collection_by_metafield.return_value = None
        api.list_custom_collections.return_value = []
        api.list_smart_collections.return_value = []
        api.create_custom_collection.return_value = {
            "id": 1001,
            "handle": f"odoo-pcat-{self.empty.id}",
            "published_at": None,
        }
        api.list_collects.return_value = []
        api.upsert_custom_collection_metafield.return_value = {}

        with patch.object(type(self.store), "_get_api_client", return_value=api):
            res = self.Service.sync_category(
                self.store, self.empty, dry_run=False, sync_membership=True
            )

        self.assertEqual(res["shopify_collection_id"], "1001")
        args, kwargs = api.create_custom_collection.call_args
        payload = args[0] if args else kwargs
        self.assertFalse(payload.get("published"))
        mapping = self.Map.search(
            [("store_id", "=", self.store.id), ("public_categ_id", "=", self.empty.id)],
            limit=1,
        )
        self.assertTrue(mapping)
        self.assertEqual(mapping.shopify_collection_id, "1001")
        self.assertEqual(mapping.sync_state, "done")
        self.assertFalse(mapping.nav_eligible)

    def test_menu_proposal_hides_empty(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        plans = [
            {
                "public_categ_id": self.parent.id,
                "name": self.parent.name,
                "path": self.parent.name,
                "parent_id": False,
                "stable_handle": f"odoo-pcat-{self.parent.id}",
                "nav_eligible": False,
                "empty": True,
            },
            {
                "public_categ_id": self.child.id,
                "name": self.child.name,
                "path": f"{self.parent.name} / {self.child.name}",
                "parent_id": self.parent.id,
                "stable_handle": f"odoo-pcat-{self.child.id}",
                "nav_eligible": True,
                "empty": False,
            },
            {
                "public_categ_id": self.empty.id,
                "name": self.empty.name,
                "path": self.empty.name,
                "parent_id": False,
                "stable_handle": f"odoo-pcat-{self.empty.id}",
                "nav_eligible": False,
                "empty": True,
            },
        ]
        proposal = self.Service._build_shop_menu_proposal(plans)
        self.assertEqual(proposal["top_level_title"], "Shop")
        # Parent included because it has eligible children
        titles = [i["title"] for i in proposal["items"]]
        self.assertIn(self.parent.name, titles)
        self.assertNotIn(self.empty.name, titles)

    def test_membership_add_remove(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        api = MagicMock()
        api.list_collects.return_value = [
            {"id": 9, "product_id": 111},
            {"id": 10, "product_id": 222},
        ]
        with patch.object(
            type(self.Service),
            "_desired_shopify_product_ids",
            return_value=([222, 333], self.env["product.template"]),
        ):
            result = self.Service._sync_membership(api, self.store, self.child, "500")
        api.create_collect.assert_called_once_with("500", 333)
        api.delete_collect.assert_called_once_with(9)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["removed"], 1)

    def test_second_run_idempotent_reuse(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale not installed")
        handle = f"odoo-pcat-{self.child.id}"
        self.Map.create(
            {
                "store_id": self.store.id,
                "public_categ_id": self.child.id,
                "shopify_collection_id": "42",
                "shopify_handle": handle,
                "sync_state": "done",
            }
        )
        api = MagicMock()
        api.get_custom_collection.return_value = {
            "id": 42,
            "handle": handle,
            "title": self.child.name,
        }
        api.list_custom_collections.return_value = []
        api.list_smart_collections.return_value = []
        api.list_collects.return_value = []
        with patch.object(type(self.store), "_get_api_client", return_value=api):
            plan = self.Service.plan_category(self.store, self.child, api=api)
            res = self.Service.sync_category(
                self.store, self.child, dry_run=False, sync_membership=False
            )
        self.assertEqual(plan["action"], "reuse")
        self.assertEqual(plan["verify_via"], "map_id")
        api.create_custom_collection.assert_not_called()
        api.update_custom_collection.assert_called()
        self.assertEqual(res["shopify_collection_id"], "42")
