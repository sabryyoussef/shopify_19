from odoo import _, api


class LocationService:
    def __init__(self, env):
        self.env = env

    @api.model
    def import_locations(self, store):
        """Import all Shopify locations for the given store."""
        api_client = store._get_api_client()

        try:
            locations = api_client.get_locations()
        except Exception as e:
            # Log and re-raise to surface the error to the user
            self.env["shopify.sync.log.mixin"].create_log(
                store=store,
                log_type="other",
                message=str(e),
                payload=False,
                status="failed",
            )
            raise

        Location = self.env["shopify.location"]
        created = 0
        updated = 0

        for loc in locations:
            shopify_location_id = str(loc.get("id")) if loc.get("id") else False
            if not shopify_location_id:
                continue

            vals = {
                "name": loc.get("name") or shopify_location_id,
                "shopify_location_id": shopify_location_id,
                "shopify_store_id": store.id,
                "active": bool(loc.get("active", True)),
            }

            existing = Location.search(
                [
                    ("shopify_location_id", "=", shopify_location_id),
                    ("shopify_store_id", "=", store.id),
                ],
                limit=1,
            )
            if existing:
                existing.write(vals)
                updated += 1
            else:
                Location.create(vals)
                created += 1

        self.env["shopify.sync.log.mixin"].create_log(
            store=store,
            log_type="other",
            message=_(
                "Imported Shopify locations: %(created)s created, %(updated)s updated."
            )
            % {"created": created, "updated": updated},
            payload={"created": created, "updated": updated},
            status="success",
        )

        return created, updated

