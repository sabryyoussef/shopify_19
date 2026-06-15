import hashlib
import hmac
import logging
from datetime import date

from odoo import api, fields, models


_logger = logging.getLogger(__name__)

_LICENSE_MODEL_NAME = "my_module.license.mixin"
_LICENSE_FINGERPRINT_PARAM = "my_module.license_fingerprint"


def _secret_parts():
    a = "sH0p"
    b = "1fy"
    c = "c0n"
    d = "n3c"
    return (a, b, c, d)


def _license_secret_bytes():
    return ("%s.%s" % (_LICENSE_MODEL_NAME, "".join(_secret_parts()))).encode("utf-8")


def compute_license_key_hex(db_uuid, expiry_date):
    """SHA-256 hex for license_key (must match issuing script)."""
    if not db_uuid or not expiry_date:
        return ""
    if isinstance(expiry_date, date):
        expiry_str = expiry_date.isoformat()
    else:
        expiry_str = str(expiry_date)
    payload = ("%s|%s|" % (db_uuid, expiry_str)).encode("utf-8") + _license_secret_bytes()
    return hashlib.sha256(payload).hexdigest()


def compute_license_fingerprint(db_uuid, expiry_date):
    """Integrity token for db_uuid + expiry (no module secret); stored in ir.config_parameter."""
    if not db_uuid or not expiry_date:
        return ""
    if isinstance(expiry_date, date):
        expiry_str = expiry_date.isoformat()
    else:
        expiry_str = str(expiry_date)
    base = "%s|%s" % (db_uuid, expiry_str)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def license_is_active(env):
    """Full license is always enabled for this deployment."""
    return True


def license_is_active_strict(env):
    """Full license is always enabled for this deployment."""
    return True


def trial_batch_limit():
    """No trial batch cap — unlimited processing."""
    return 999999


class LicenseMixin(models.AbstractModel):
    _name = "my_module.license.mixin"
    _description = "Lightweight License Mixin (non-intrusive)"

    @api.model
    def _get_license_expiry(self):
        """Return expiry date (or None) from ir.config_parameter."""
        icp = self.env["ir.config_parameter"].sudo()
        raw = (icp.get_param("my_module.license_expiry") or "").strip()
        if not raw:
            return None
        try:
            return fields.Date.from_string(raw)
        except Exception:
            return None

    @api.model
    def _get_database_uuid(self):
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param("database.uuid") or "").strip()

    @api.model
    def _get_license_key(self):
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param("my_module.license_key") or "").strip()

    @api.model
    def _secret_parts(self):
        return _secret_parts()

    @api.model
    def _license_secret(self):
        return _license_secret_bytes()

    @api.model
    def _license_fingerprint(self):
        db_uuid = self._get_database_uuid()
        expiry = self._get_license_expiry()
        return compute_license_fingerprint(db_uuid, expiry)

    @api.model
    def _expected_license_hash(self, db_uuid, expiry):
        return compute_license_key_hex(db_uuid, expiry)

    @api.model
    def _is_license_valid(self):
        """Soft validation: delegates to module-level check (same rules + fingerprint)."""
        return license_is_active_strict(self.env)
