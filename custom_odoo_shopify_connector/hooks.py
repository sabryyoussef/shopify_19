"""Module lifecycle hooks for Shopify Connector."""


def post_init_hook(env_or_cr, registry=None):
    """Technical Shopify users must not appear as commercial customers (Sales → Customers)."""
    from odoo import SUPERUSER_ID, api

    # Odoo 19 calls post-init hooks with env; older versions may pass (cr, registry).
    if registry is None and hasattr(env_or_cr, "cr"):
        env = env_or_cr
    else:
        env = api.Environment(env_or_cr, SUPERUSER_ID, {})

    user = env.ref(
        "custom_odoo_shopify_connector.user_shopify",
        raise_if_not_found=False,
    )
    if user and user.partner_id:
        user.partner_id.write({"customer_rank": 0, "supplier_rank": 0})

    tech_users = env["res.users"].sudo().search(
        [
            (
                "login",
                "in",
                ("shopify.connector.import", "shopify@system.local"),
            )
        ]
    )
    for tech_user in tech_users:
        if tech_user.partner_id:
            tech_user.partner_id.write({"customer_rank": 0, "supplier_rank": 0})
