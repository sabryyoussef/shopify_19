import requests

from config import get_config


class ShopifyGraphQLError(RuntimeError):
    pass


def graphql_query(query, variables=None):
    config = get_config()
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": config["access_token"],
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        response = requests.post(
            config["graphql_url"],
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ShopifyGraphQLError("Network error calling Shopify: %s" % exc) from exc

    if response.status_code == 401:
        raise ShopifyGraphQLError(
            "401 Unauthorized: access token is invalid, expired, or revoked."
        )
    if response.status_code == 403:
        raise ShopifyGraphQLError(
            "403 Forbidden: token may be missing required Admin API scopes."
        )
    if response.status_code == 404:
        raise ShopifyGraphQLError(
            "404 Not Found: check SHOPIFY_STORE_DOMAIN and SHOPIFY_API_VERSION."
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ShopifyGraphQLError(
            "Non-JSON response (HTTP %s): %s"
            % (response.status_code, response.text[:200])
        ) from exc

    if response.status_code >= 400:
        raise ShopifyGraphQLError(
            "HTTP %s from Shopify: %s" % (response.status_code, body)
        )

    if body.get("errors"):
        raise ShopifyGraphQLError("GraphQL errors: %s" % body["errors"])

    return body.get("data")
