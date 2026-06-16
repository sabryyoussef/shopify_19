"""Read-only test: validate token and fetch basic shop info."""

from shopify_graphql import ShopifyGraphQLError, graphql_query

SHOP_QUERY = """
{
  shop {
    name
    myshopifyDomain
    primaryDomain {
      host
    }
  }
}
"""


def main():
    print("Shopify Admin API — shop info (read-only)")
    print("-" * 44)

    try:
        data = graphql_query(SHOP_QUERY)
    except ShopifyGraphQLError as exc:
        print("FAILED:", exc)
        return 1

    shop = data.get("shop") or {}
    print("Shop name:          %s" % shop.get("name", "(missing)"))
    print("myshopify domain:   %s" % shop.get("myshopifyDomain", "(missing)"))
    primary = shop.get("primaryDomain") or {}
    print("Primary domain:     %s" % primary.get("host", "(missing)"))
    print("-" * 44)
    print("SUCCESS: token is valid and shop info was retrieved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
