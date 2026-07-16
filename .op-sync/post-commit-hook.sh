#!/bin/bash
set -euo pipefail
cd /opt/odoo/custom-addons/shopify_19
exec python3 .op-sync/queue-commit.py
