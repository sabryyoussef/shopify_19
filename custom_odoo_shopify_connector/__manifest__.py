{
    "name": "Shopify Connector free trail version",
    "version": "19.0.1.0.5",
    "summary": "Integrate Odoo with Shopify to sync products, customers, orders, and inventory.",
    "description": """
Shopify Connector for Odoo

This module integrates Odoo with Shopify and allows automatic synchronization of:

• Products and variants
• Customers
• Orders
• Inventory levels
• Webhook-based order import
• Queue-based background processing

Features:
- Multi Shopify store support
- Webhook order synchronization
- Product variant mapping
- Real-time inventory sync
- Order processing queue
- API pagination support
- Secure webhook verification
""",
    "author": "Gultaj Khan",
    "maintainer": "Gultaj Khan",
    "website": "https://github.com/gultajkhan",
    "support": "gultajkhan980@gmail.com",
    "license": "LGPL-3",
    "price": 0.0,
    "currency": "USD",
    "category": "Sales",
    "depends": [
        "base",
        "sale_management",
        "stock",
        "product",
        "account",
        "delivery",
        "stock_delivery",
    ],
     "data": [
        "security/shopify_security.xml",
        "security/ir.model.access.csv",
        "data/shopify_user.xml",
        "data/webhook.xml",
        "data/product_queue_sequence.xml",
        "data/price_update_queue_sequence.xml",
        "data/cron_jobs.xml",
        "data/cron.xml",
        "data/operations.xml",
        "data/sale_auto_workflow_data.xml",
        "views/shopify_store_view.xml",
        "views/shopify_settings_views.xml",
        "views/trial_popup_views.xml",
        "views/order_queue_view.xml",
        "views/shopify_webhook_event_views.xml",
        "views/shopify_queue_view.xml",
        "views/product_queue_view.xml",
        "views/shopify_product_sync_check_view.xml",
        "views/shopify_product_layer_views.xml",
        "views/shopify_location_views.xml",
        "views/sale_auto_workflow_views.xml",
        "views/payment_gateway_views.xml",
        "views/financial_status_views.xml",
        "views/delivery_carrier_view.xml",
        "views/stock_picking_view.xml",
        "views/stock_package_views.xml",
        "views/sale_order_view.xml",
        "views/shopify_cancel_wizard.xml",
        "views/shopify_exchange_wizard.xml",
        "views/account_move_view.xml",
        "views/shopify_refund_wizard.xml",
        "views/shopify_import_stock_wizard.xml",
        "views/shopify_customer_import_view.xml",
        "views/shopify_stock_image_queue_views.xml",
        "views/shopify_update_product_wizard.xml",
        "views/shopify_extra_models_views.xml",
        "views/dashboard_view.xml",
        "views/shopify_actions.xml",
        "views/res_partner_contacts_view.xml",
        "views/menu.xml",
        "views/shopify_menu.xml",

    ],
    "images": ["static/description/icon.png"],
    "external_dependencies": {"python": ["openpyxl", "passlib"]},
    "installable": True,
    "application": True,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
