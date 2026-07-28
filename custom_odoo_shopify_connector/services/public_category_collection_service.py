# -*- coding: utf-8 -*-
"""Odoo product.public.category → Shopify custom (manual) collection sync.

Migration A:
- Never adopt collections by title alone.
- Reuse only via mapping ID, verified handle ``odoo-pcat-{id}``, or metafield.
- Never reuse smart collections.
- Create missing unpublished custom collections.
- Delete nothing.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

META_NAMESPACE = "odoo_petspot"
META_KEY_PUBLIC_CATEG_ID = "public_categ_id"
HANDLE_PREFIX = "odoo-pcat-"
ALLOWED_SHOP_MARKERS = ("ucbah1-5e", "shopify.drpaws.ai")
REJECTED_SHOP_MARKERS = ("izone-eg", "izone")


class ShopifyPublicCategoryService(models.AbstractModel):
    _name = "shopify.public.category.service"
    _description = "Shopify Public Category Collection Sync Service"

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def _assert_petspot_store(self, store):
        store.ensure_one()
        url = (store.shop_url or "").lower()
        if any(bad in url for bad in REJECTED_SHOP_MARKERS):
            raise UserError(_("Refusing to sync collections for non-PetSpot store: %s") % store.shop_url)
        if not any(ok in url for ok in ALLOWED_SHOP_MARKERS):
            raise UserError(
                _("Store URL is not an approved Pet Spot host (%s). Refusing sync.")
                % store.shop_url
            )

    def _require_public_category_model(self):
        if "product.public.category" not in self.env:
            raise UserError(_("website_sale / product.public.category is not installed."))

    def _stable_handle(self, public_categ):
        return f"{HANDLE_PREFIX}{int(public_categ.id)}"

    def _category_path(self, categ):
        parts = []
        cur = categ
        seen = set()
        while cur and cur.id not in seen:
            seen.add(cur.id)
            parts.append(cur.name or "")
            cur = cur.parent_id
        return " / ".join(reversed([p for p in parts if p]))

    # ------------------------------------------------------------------
    # Desired membership
    # ------------------------------------------------------------------
    def _desired_shopify_product_ids(self, store, public_categ):
        """Shopify product IDs for templates in this public category that are mapped."""
        Product = self.env["product.template"]
        ProductMap = self.env["shopify.product.map"]
        tmpl_ids = Product.search([("public_categ_ids", "in", [public_categ.id])]).ids
        if not tmpl_ids:
            return [], []
        maps = ProductMap.search(
            [
                ("store_id", "=", store.id),
                ("product_tmpl_id", "in", tmpl_ids),
                ("shopify_product_id", "!=", False),
            ]
        )
        mapped_tmpl_ids = set(maps.mapped("product_tmpl_id").ids)
        missing_tmpls = Product.browse([i for i in tmpl_ids if i not in mapped_tmpl_ids])
        shopify_ids = []
        for m in maps:
            try:
                shopify_ids.append(int(m.shopify_product_id))
            except (TypeError, ValueError):
                continue
        return sorted(set(shopify_ids)), missing_tmpls

    def _nav_eligible(self, mapped_shopify_ids):
        return bool(mapped_shopify_ids)

    # ------------------------------------------------------------------
    # Identity verification (no title adoption)
    # ------------------------------------------------------------------
    def _find_verified_custom_collection(self, api, public_categ, existing_map=None):
        """Return custom collection dict if identity is verified; else None.

        Verification order:
        1) Mapping shopify_collection_id still exists as custom collection
        2) Custom collection handle == odoo-pcat-{id}
        3) Custom collection metafield odoo_petspot.public_categ_id == id

        Smart collections are never returned.
        Title match alone is never accepted.
        """
        handle = self._stable_handle(public_categ)
        categ_id_str = str(public_categ.id)

        if existing_map and existing_map.shopify_collection_id:
            try:
                col = api.get_custom_collection(existing_map.shopify_collection_id)
            except Exception as exc:
                _logger.warning(
                    "Mapped collection %s missing/inaccessible for categ %s: %s",
                    existing_map.shopify_collection_id,
                    public_categ.id,
                    exc,
                )
                col = None
            if col and str(col.get("id")) == str(existing_map.shopify_collection_id):
                return col, "map_id"

        # Handle exact match among custom collections
        try:
            by_handle = api.find_custom_collection_by_handle(handle)
        except Exception as exc:
            _logger.warning("Handle lookup failed for %s: %s", handle, exc)
            by_handle = None
        if by_handle:
            meta_ok = self._collection_metafield_matches(api, by_handle["id"], categ_id_str)
            # Handle itself encodes odoo id → accept; stamp metafield on execute
            if meta_ok or str(by_handle.get("handle")) == handle:
                return by_handle, "handle"

        # Metafield scan (custom only) — expensive; used sparingly
        try:
            found = api.find_custom_collection_by_metafield(
                META_NAMESPACE, META_KEY_PUBLIC_CATEG_ID, categ_id_str
            )
        except Exception as exc:
            _logger.warning("Metafield scan failed for categ %s: %s", public_categ.id, exc)
            found = None
        if found:
            return found, "metafield"

        return None, None

    def _collection_metafield_matches(self, api, collection_id, categ_id_str):
        try:
            metas = api.get_custom_collection_metafields(collection_id) or []
        except Exception:
            return False
        for mf in metas:
            if (
                mf.get("namespace") == META_NAMESPACE
                and mf.get("key") == META_KEY_PUBLIC_CATEG_ID
                and str(mf.get("value")) == str(categ_id_str)
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # Planning / dry-run
    # ------------------------------------------------------------------
    def plan_category(self, store, public_categ, api=None):
        self._assert_petspot_store(store)
        self._require_public_category_model()
        public_categ.ensure_one()
        Map = self.env["shopify.public.category.map"]
        existing = Map.search(
            [("store_id", "=", store.id), ("public_categ_id", "=", public_categ.id)],
            limit=1,
        )
        api = api or store._get_api_client()
        desired_ids, missing = self._desired_shopify_product_ids(store, public_categ)
        verified, verify_via = self._find_verified_custom_collection(api, public_categ, existing)

        # Title conflicts (informational only — never auto-adopt)
        title_conflicts = []
        try:
            for col in api.list_custom_collections(limit=250):
                if (col.get("title") or "").strip().lower() == (public_categ.name or "").strip().lower():
                    if not verified or str(col.get("id")) != str(verified.get("id")):
                        title_conflicts.append(
                            {
                                "id": col.get("id"),
                                "handle": col.get("handle"),
                                "title": col.get("title"),
                                "reason": "same_title_unverified",
                            }
                        )
            for col in api.list_smart_collections(limit=250):
                if (col.get("title") or "").strip().lower() == (public_categ.name or "").strip().lower():
                    title_conflicts.append(
                        {
                            "id": col.get("id"),
                            "handle": col.get("handle"),
                            "title": col.get("title"),
                            "type": "smart",
                            "reason": "smart_same_title_ignored",
                        }
                    )
        except Exception as exc:
            _logger.warning("Conflict scan failed: %s", exc)

        action = "reuse" if verified else "create"
        plan = {
            "public_categ_id": public_categ.id,
            "name": public_categ.name,
            "path": self._category_path(public_categ),
            "parent_id": public_categ.parent_id.id if public_categ.parent_id else False,
            "stable_handle": self._stable_handle(public_categ),
            "action": action,
            "verify_via": verify_via,
            "shopify_collection_id": verified.get("id") if verified else None,
            "existing_map_id": existing.id if existing else None,
            "desired_membership_count": len(desired_ids),
            "desired_shopify_product_ids_sample": desired_ids[:20],
            "missing_shopify_products": [
                {"id": t.id, "name": t.display_name} for t in missing[:50]
            ],
            "missing_shopify_product_count": len(missing),
            "empty": len(desired_ids) == 0 and len(missing) == 0,
            "nav_eligible": self._nav_eligible(desired_ids),
            "title_conflicts": title_conflicts,
            "published_target": False,
        }
        return plan

    def plan_all(self, store):
        self._assert_petspot_store(store)
        self._require_public_category_model()
        api = store._get_api_client()
        cats = self.env["product.public.category"].search([], order="parent_path, sequence, id")
        plans = []
        for cat in cats:
            plans.append(self.plan_category(store, cat, api=api))
            time.sleep(0.05)
        report = self._summarize_plans(store, plans)
        return report

    def _summarize_plans(self, store, plans):
        menu_proposal = self._build_shop_menu_proposal(plans)
        return {
            "store_id": store.id,
            "store_url": store.shop_url,
            "migration": "A",
            "ts": fields.Datetime.now().isoformat(),
            "totals": {
                "categories": len(plans),
                "to_create": sum(1 for p in plans if p["action"] == "create"),
                "to_reuse_verified": sum(1 for p in plans if p["action"] == "reuse"),
                "empty": sum(1 for p in plans if p["empty"]),
                "nav_eligible": sum(1 for p in plans if p["nav_eligible"]),
                "nav_hidden_empty": sum(1 for p in plans if p["empty"] or not p["nav_eligible"]),
                "conflicts": sum(1 for p in plans if p["title_conflicts"]),
                "missing_product_rows": sum(p["missing_shopify_product_count"] for p in plans),
            },
            "plans": plans,
            "shop_menu_proposal": menu_proposal,
            "notes": [
                "Migration A: create unpublished custom collections; never adopt by title.",
                "Smart collections with same titles are conflicts for review — left untouched.",
                "Shop menu is proposal-only; no live menu writes in Phase 2.",
                "Collections stay unpublished (published=false).",
            ],
        }

    def _build_shop_menu_proposal(self, plans):
        """Nested Shop menu proposal; empty/non-eligible categories omitted from nav."""
        by_id = {p["public_categ_id"]: p for p in plans}
        roots = [p for p in plans if not p["parent_id"]]

        def node(p):
            children = [
                node(c)
                for c in plans
                if c["parent_id"] == p["public_categ_id"] and c.get("nav_eligible")
            ]
            return {
                "title": p["name"],
                "public_categ_id": p["public_categ_id"],
                "handle": p["stable_handle"],
                "nav_eligible": p["nav_eligible"],
                "include_in_menu": bool(p["nav_eligible"]),
                "children": children,
            }

        items = [node(r) for r in roots if r.get("nav_eligible")]
        # Parents with only eligible children should still appear
        for r in roots:
            if r.get("nav_eligible"):
                continue
            kids = [
                node(c)
                for c in plans
                if c["parent_id"] == r["public_categ_id"] and c.get("nav_eligible")
            ]
            if kids:
                items.append(
                    {
                        "title": r["name"],
                        "public_categ_id": r["public_categ_id"],
                        "handle": r["stable_handle"],
                        "nav_eligible": False,
                        "include_in_menu": True,
                        "reason": "parent_of_eligible_children",
                        "children": kids,
                    }
                )
        return {
            "top_level_title": "Shop",
            "backup_required_before_live": True,
            "items": items,
            "excluded_empty_or_unmapped": [
                {"id": p["public_categ_id"], "name": p["name"], "path": p["path"]}
                for p in plans
                if not p["nav_eligible"]
            ],
        }

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    def sync_category(self, store, public_categ, dry_run=True, sync_membership=True):
        plan = self.plan_category(store, public_categ)
        Map = self.env["shopify.public.category.map"]
        mapping = Map.search(
            [("store_id", "=", store.id), ("public_categ_id", "=", public_categ.id)],
            limit=1,
        )
        if not mapping:
            mapping = Map.create(
                {
                    "store_id": store.id,
                    "public_categ_id": public_categ.id,
                    "sync_state": "pending",
                }
            )
        mapping.write(
            {
                "last_dry_run_json": json.dumps(plan, ensure_ascii=False, default=str),
                "nav_eligible": plan["nav_eligible"],
            }
        )
        if dry_run:
            mapping.sync_state = "pending"
            return {"dry_run": True, "plan": plan, "mapping_id": mapping.id}

        api = store._get_api_client()
        try:
            if plan["action"] == "reuse":
                collection_id = str(plan["shopify_collection_id"])
                # Update title/description; keep unpublished
                body_html = public_categ.website_description or ""
                api.update_custom_collection(
                    collection_id,
                    {
                        "id": int(collection_id),
                        "title": public_categ.name,
                        "body_html": body_html,
                        "handle": plan["stable_handle"],
                        "published": False,
                    },
                )
            else:
                created = api.create_custom_collection(
                    {
                        "title": public_categ.name,
                        "body_html": public_categ.website_description or "",
                        "handle": plan["stable_handle"],
                        "published": False,
                    }
                )
                collection_id = str(created.get("id"))

            api.upsert_custom_collection_metafield(
                collection_id,
                META_NAMESPACE,
                META_KEY_PUBLIC_CATEG_ID,
                "single_line_text_field",
                str(public_categ.id),
            )

            membership_result = None
            if sync_membership:
                membership_result = self._sync_membership(
                    api, store, public_categ, collection_id
                )

            mapping.write(
                {
                    "shopify_collection_id": collection_id,
                    "shopify_handle": plan["stable_handle"],
                    "published_on_shopify": False,
                    "nav_eligible": plan["nav_eligible"],
                    "last_sync_at": fields.Datetime.now(),
                    "sync_state": "done",
                    "sync_error": False,
                }
            )
            return {
                "dry_run": False,
                "plan": plan,
                "mapping_id": mapping.id,
                "shopify_collection_id": collection_id,
                "membership": membership_result,
            }
        except Exception as exc:
            mapping.write({"sync_state": "failed", "sync_error": str(exc)[:2000]})
            _logger.exception(
                "Public category sync failed store=%s categ=%s", store.id, public_categ.id
            )
            raise

    def _sync_membership(self, api, store, public_categ, collection_id):
        desired_ids, missing = self._desired_shopify_product_ids(store, public_categ)
        desired_set = set(desired_ids)
        current = api.list_collects(collection_id=collection_id) or []
        current_by_product = {}
        for col in current:
            try:
                pid = int(col.get("product_id"))
            except (TypeError, ValueError):
                continue
            current_by_product[pid] = col

        to_add = sorted(desired_set - set(current_by_product))
        to_remove = sorted(set(current_by_product) - desired_set)

        added = removed = 0
        errors = []
        for pid in to_add:
            try:
                api.create_collect(collection_id, pid)
                added += 1
                time.sleep(0.15)
            except Exception as exc:
                msg = str(exc)
                # Idempotent: already in collection is success
                if "already" in msg.lower() or "taken" in msg.lower() or "422" in msg:
                    added += 1
                    continue
                errors.append({"product_id": pid, "op": "add", "error": msg[:300]})
                time.sleep(1.0)
        for pid in to_remove:
            try:
                collect_id = current_by_product[pid].get("id")
                if collect_id:
                    api.delete_collect(collect_id)
                    removed += 1
                    time.sleep(0.12)
            except Exception as exc:
                errors.append({"product_id": pid, "op": "remove", "error": str(exc)[:300]})

        return {
            "desired": len(desired_set),
            "added": added,
            "removed": removed,
            "unchanged": len(desired_set & set(current_by_product)),
            "missing_odoo_products": [
                {"id": t.id, "name": t.display_name} for t in missing[:50]
            ],
            "errors": errors[:50],
        }

    def sync_all(self, store, dry_run=True, sync_membership=True):
        self._assert_petspot_store(store)
        report = self.plan_all(store)
        if dry_run:
            return report

        results = []
        PublicCateg = self.env["product.public.category"]
        for plan in report["plans"]:
            categ = PublicCateg.browse(plan["public_categ_id"])
            try:
                res = self.sync_category(
                    store, categ, dry_run=False, sync_membership=sync_membership
                )
                results.append({"ok": True, **{k: res.get(k) for k in (
                    "shopify_collection_id", "mapping_id", "membership"
                )}, "public_categ_id": categ.id, "name": categ.name})
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "public_categ_id": categ.id,
                        "name": categ.name,
                        "error": str(exc)[:500],
                    }
                )
            time.sleep(0.15)
        report["execute_results"] = results
        report["execute_ok"] = sum(1 for r in results if r.get("ok"))
        report["execute_failed"] = sum(1 for r in results if not r.get("ok"))
        return report

    def sync_product_memberships(self, store, product_tmpl):
        """After product export: ensure Collects match public_categ_ids (mapped only)."""
        self._assert_petspot_store(store)
        if "public_categ_ids" not in product_tmpl._fields:
            return {"skipped": True, "reason": "no_public_categ_ids"}
        Map = self.env["shopify.public.category.map"]
        results = []
        for categ in product_tmpl.public_categ_ids:
            mapping = Map.search(
                [
                    ("store_id", "=", store.id),
                    ("public_categ_id", "=", categ.id),
                    ("shopify_collection_id", "!=", False),
                ],
                limit=1,
            )
            if not mapping:
                results.append({"categ_id": categ.id, "skipped": True, "reason": "no_mapping"})
                continue
            api = store._get_api_client()
            mem = self._sync_membership(
                api, store, categ, mapping.shopify_collection_id
            )
            mapping.nav_eligible = self._nav_eligible(
                self._desired_shopify_product_ids(store, categ)[0]
            )
            results.append({"categ_id": categ.id, "membership": mem})
        return {"results": results}
