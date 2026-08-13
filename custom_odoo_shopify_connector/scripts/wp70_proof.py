#!/usr/bin/env python3
"""WP70 requirements proof script — verifies module version, DB fields, and config."""
import json
import subprocess
import sys

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def psql(query):
    result = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-U", "odoo", "-d", "odoo_test", "-t", "-A", "-c", query],
        capture_output=True,
        text=True,
        env={"PGPASSWORD": "odoo", **dict(__import__("os").environ)},
    )
    return result.stdout.strip()


# Module version
version = psql(
    "SELECT latest_version FROM ir_module_module WHERE name='custom_odoo_shopify_connector';"
)
check("Module installed v19.0.1.0.5", version == "19.0.1.0.5", version)

# New sale.order fields
for field in ("shopify_amount_paid", "shopify_exchange_price_diff"):
    exists = psql(
        f"SELECT count(*) FROM ir_model_fields WHERE model='sale.order' AND name='{field}';"
    )
    check(f"sale.order.{field} field", exists == "1")

# New shopify.store fields
store_fields = [
    "cancel_sync_mode",
    "order_edit_sync_mode",
    "partial_payment_mode",
    "refund_restock_mode",
    "refund_restock_validate",
    "default_return_warehouse_id",
    "outbound_refund_restock_type",
]
for field in store_fields:
    exists = psql(
        f"SELECT count(*) FROM ir_model_fields WHERE model='shopify.store' AND name='{field}';"
    )
    check(f"shopify.store.{field} field", exists == "1")

# account.payment shopify_transaction_id
exists = psql(
    "SELECT count(*) FROM ir_model_fields WHERE model='account.payment' AND name='shopify_transaction_id';"
)
check("account.payment.shopify_transaction_id field", exists == "1")

# sync.log refund operation
exists = psql(
    "SELECT count(*) FROM ir_model_fields WHERE model='shopify.sync.log' AND name='operation';"
)
check("shopify.sync.log operation field", exists == "1")

# Service files exist
import os

base = "/opt/odoo/custom-addons/shopify_19/custom_odoo_shopify_connector/services"
for svc in ("order_update_service.py", "return_picking_service.py"):
    check(f"Service {svc}", os.path.isfile(os.path.join(base, svc)))

# Test files
test_base = "/opt/odoo/custom-addons/shopify_19/custom_odoo_shopify_connector/tests"
for test in (
    "test_auto_create_product.py",
    "test_partial_payment.py",
    "test_cancel_credit_note.py",
    "test_order_update_sync.py",
    "test_refund_restock.py",
):
    check(f"Test {test}", os.path.isfile(os.path.join(test_base, test)))

# Remediation plan doc
check(
    "REMEDIATION_PLAN.md",
    os.path.isfile(
        "/opt/odoo/custom-addons/shopify_19/custom_odoo_shopify_connector/docs/wp70/REMEDIATION_PLAN.md"
    ),
)

failed = [c for c in CHECKS if not c["ok"]]
print()
print(f"Total: {len(CHECKS)} checks, {len(CHECKS) - len(failed)} passed, {len(failed)} failed")
if failed:
    sys.exit(1)
print("ALL REQUIREMENTS VERIFIED")
