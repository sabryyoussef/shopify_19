"""Read-only test: fetch the first 5 orders."""

from shopify_graphql import ShopifyGraphQLError, graphql_query

ORDERS_QUERY = """
{
  orders(first: 5, sortKey: CREATED_AT, reverse: true) {
    edges {
      node {
        id
        name
        createdAt
        displayFinancialStatus
        totalPriceSet {
          shopMoney {
            amount
            currencyCode
          }
        }
      }
    }
  }
}
"""


def main():
    print("Shopify Admin API — orders read (read-only, first 5)")
    print("-" * 44)

    try:
        data = graphql_query(ORDERS_QUERY)
    except ShopifyGraphQLError as exc:
        print("FAILED:", exc)
        return 1

    edges = (data.get("orders") or {}).get("edges") or []
    if not edges:
        print("No orders returned (store may have zero orders).")
    else:
        for index, edge in enumerate(edges, start=1):
            order = edge.get("node") or {}
            money = (order.get("totalPriceSet") or {}).get("shopMoney") or {}
            amount = money.get("amount", "?")
            currency = money.get("currencyCode", "?")
            print(
                "%d. %s | id=%s | created=%s | financial=%s | total=%s %s"
                % (
                    index,
                    order.get("name", "(no name)"),
                    order.get("id", "(no id)"),
                    order.get("createdAt", "(no date)"),
                    order.get("displayFinancialStatus", "(unknown)"),
                    amount,
                    currency,
                )
            )

    print("-" * 44)
    print("SUCCESS: orders query completed (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
