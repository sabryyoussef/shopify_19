#!/usr/bin/env bash
# Apply Phase 1 websocket/longpolling config. Run: sudo bash apply-phase1.sh
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="/opt/odoo-log-report-2026-06-28/backups/phase1_pre_apply_${STAMP}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo" >&2
  exit 1
fi

mkdir -p "$BACKUP"/{etc,nginx}
cp -a /etc/odoo.conf "$BACKUP/etc/"
cp -a /etc/nginx/sites-available/odoo "$BACKUP/nginx/"

cp -a "$DEPLOY_DIR/etc/odoo.conf" /etc/odoo.conf
cp -a "$DEPLOY_DIR/nginx/odoo" /etc/nginx/sites-available/odoo

nginx -t
systemctl reload nginx
systemctl restart odoo

echo "Phase 1 applied. Backup: $BACKUP"
echo "Validate:"
echo "  curl -s http://127.0.0.1:8072/websocket/health"
echo "  curl -sk https://127.0.0.1/websocket/health -H 'Host: 37-61-219-169.nip.io'"
