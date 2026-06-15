# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _

_logger = logging.getLogger(__name__)

# Retries for fulfillment API calls (HTTP 429 and transient errors). API client
# respects Retry-After header and uses exponential backoff when 429 is returned.
FULFILLMENT_MAX_RETRIES = 3


class ShopifyFulfillmentService:
    """
    Service to create Shopify fulfillments with retry and rate-limit handling.
    Supports 10k+ orders: 3 retries, HTTP 429 handling with Retry-After support.
    """

    def __init__(self, env):
        self.env = env

    def create_fulfillment(self, api_client, order_id, tracking_number, carrier, notify_customer=True):
        """
        Create a single fulfillment on Shopify for the given order.

        Uses the API client's built-in retry (3 attempts, respects Retry-After on 429).
        :param api_client: ShopifyAPI instance (from store._get_api_client())
        :param order_id: Shopify order ID (string or int)
        :param tracking_number: Tracking number string
        :param carrier: delivery.carrier record or dict with shopify_tracking_company or name
        :param notify_customer: Whether to notify the customer (default True)
        :return: API response dict or None
        """
        if not api_client or not order_id or not tracking_number:
            _logger.warning("Shopify fulfillment: missing api_client, order_id or tracking_number")
            return None

        tracking_company = self._resolve_tracking_company(carrier)
        payload = {
            "fulfillment": {
                "tracking_number": str(tracking_number).strip(),
                "tracking_company": tracking_company or "Other",
                "notify_customer": bool(notify_customer),
            }
        }
        path = "/orders/%s/fulfillments.json" % order_id
        try:
            response = api_client._request(
                "POST",
                path,
                data=payload,
                max_retries=FULFILLMENT_MAX_RETRIES,
            )
            _logger.debug(
                "Shopify fulfillment created for order %s tracking %s",
                order_id,
                payload["fulfillment"].get("tracking_number"),
            )
            return response
        except Exception as e:
            _logger.exception("Shopify fulfillment failed for order %s: %s", order_id, e)
            raise

    def _resolve_tracking_company(self, carrier):
        """Get tracking company string from carrier record or dict."""
        if not carrier:
            return None
        if hasattr(carrier, "shopify_tracking_company") and carrier.shopify_tracking_company:
            return (carrier.shopify_tracking_company or "").strip()
        if hasattr(carrier, "name") and carrier.name:
            return (carrier.name or "").strip()
        if isinstance(carrier, dict):
            return (
                (carrier.get("shopify_tracking_company") or carrier.get("name") or "").strip()
                or None
            )
        return None
