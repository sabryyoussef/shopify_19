def migrate(cr, version):
    """Keep Shopify technical users' partners out of customer lists (customer_rank)."""
    cr.execute(
        """
        UPDATE res_partner p
        SET customer_rank = 0,
            supplier_rank = 0
        FROM res_users u
        WHERE u.partner_id = p.id
          AND u.active = TRUE
          AND u.login IN ('shopify@system.local', 'shopify.connector.import')
        """
    )
