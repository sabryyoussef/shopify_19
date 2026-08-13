"""P1 — Shopify webhook registration / reconciliation.

Controlled, idempotent management of the store's webhook subscriptions:
  * Read desired topics -> callback address (from the store base URL).
  * Read currently registered Shopify webhooks.
  * Detect missing registrations and incorrect callback URLs.
  * Register only missing / repair only incorrect entries (no duplicates).
  * Never auto-delete unknown/extra subscriptions (report only).

`plan()` is read-only. `reconcile(dry_run=True)` reports without writing;
`reconcile(dry_run=False)` performs the controlled create/update writes and is
the ONLY method that writes to Shopify. Callers gate live registration on the
Test/UAT target (never Production).
"""
import logging

_logger = logging.getLogger(__name__)

# Desired topic -> local callback path. Order/fulfillment topics share the main
# operation-aware order endpoint (routing is decided from X-Shopify-Topic).
DESIRED_TOPICS = {
    "orders/create": "/shopify/webhook/order",
    "orders/updated": "/shopify/webhook/order",
    "orders/cancelled": "/shopify/webhook/order_cancelled",
    "fulfillments/create": "/shopify/webhook/order",
    "fulfillments/update": "/shopify/webhook/order",
    "refunds/create": "/shopify/webhook/refund_created",
}


class ShopifyWebhookRegistrationService:
    def __init__(self, env):
        self.env = env

    # ------------------------------------------------------------------
    def _base_url(self, store):
        base = (getattr(store, "webhook_base_url", "") or "").strip()
        if not base:
            base = (
                self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
            ).strip()
        return base.rstrip("/")

    def desired_addresses(self, store):
        base = self._base_url(store)
        return {topic: "%s%s" % (base, path) for topic, path in DESIRED_TOPICS.items()}

    @staticmethod
    def _norm(url):
        return (url or "").strip().rstrip("/")

    # ------------------------------------------------------------------
    def plan(self, store, client=None):
        """Read-only diff of desired vs registered webhooks.

        Returns dict: {https_ok, base_url, missing, incorrect, ok, extra}.
        """
        base = self._base_url(store)
        desired = self.desired_addresses(store)
        report = {
            "base_url": base,
            "https_ok": base.lower().startswith("https://"),
            "missing": [],
            "incorrect": [],
            "ok": [],
            "extra": [],
        }
        client = client or store._get_api_client()
        existing = client.get_webhooks() or []
        by_topic = {}
        for wh in existing:
            by_topic.setdefault(wh.get("topic"), []).append(wh)

        for topic, address in desired.items():
            matches = by_topic.get(topic) or []
            if not matches:
                report["missing"].append({"topic": topic, "address": address})
                continue
            exact = [w for w in matches if self._norm(w.get("address")) == self._norm(address)]
            if exact:
                report["ok"].append({"topic": topic, "id": exact[0].get("id"), "address": address})
            else:
                report["incorrect"].append(
                    {
                        "topic": topic,
                        "id": matches[0].get("id"),
                        "current": matches[0].get("address"),
                        "desired": address,
                    }
                )

        for topic, whs in by_topic.items():
            if topic not in desired:
                for w in whs:
                    report["extra"].append({"topic": topic, "id": w.get("id"),
                                            "address": w.get("address")})
        return report

    # ------------------------------------------------------------------
    def reconcile(self, store, dry_run=True, client=None):
        """Register missing and repair incorrect subscriptions.

        Safety: requires an https base URL and a configured webhook_secret before
        performing any live write. Never deletes extra subscriptions.
        """
        client = client or store._get_api_client()
        report = self.plan(store, client=client)
        report["dry_run"] = dry_run
        report["created"] = []
        report["updated"] = []
        report["blocked"] = None

        if not report["https_ok"]:
            report["blocked"] = "callback base URL is not https (%s)" % (report["base_url"] or "unset")
            return report
        if not store.webhook_secret:
            report["blocked"] = "store webhook_secret is not configured"
            return report

        if dry_run:
            return report

        for item in report["missing"]:
            wh = client.create_webhook(item["topic"], item["address"])
            report["created"].append({"topic": item["topic"], "id": (wh or {}).get("id"),
                                      "address": item["address"]})
        for item in report["incorrect"]:
            wh = client.update_webhook(item["id"], item["desired"])
            report["updated"].append({"topic": item["topic"], "id": item["id"],
                                      "address": item["desired"]})
        _logger.info(
            "Webhook reconcile store=%s created=%s updated=%s (extras left untouched=%s)",
            store.id, len(report["created"]), len(report["updated"]), len(report["extra"]),
        )
        return report
