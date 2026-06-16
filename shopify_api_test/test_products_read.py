"""Read-only test: fetch the first 10 products."""

from shopify_graphql import ShopifyGraphQLError, graphql_query

PRODUCTS_QUERY = """
{
  products(first: 10, sortKey: UPDATED_AT, reverse: true) {
    edges {
      node {
        id
        title
        handle
        status
        vendor
        productType
        totalInventory
        createdAt
        updatedAt
        variants(first: 5) {
          edges {
            node {
              id
              title
              sku
              price
              inventoryQuantity
            }
          }
        }
      }
    }
  }
}
"""


def _variant_sku_missing(sku):
    return not (sku or "").strip()


def main():
    print("Shopify Admin API — products read (read-only, first 10)")
    print("-" * 44)

    try:
        data = graphql_query(PRODUCTS_QUERY)
    except ShopifyGraphQLError as exc:
        print("FAILED:", exc)
        return 1

    edges = (data.get("products") or {}).get("edges") or []
    missing_sku_products = []
    low_inventory_products = []

    if not edges:
        print("No products returned (store may have zero products).")
    else:
        for index, edge in enumerate(edges, start=1):
            product = edge.get("node") or {}
            title = product.get("title", "(no title)")
            handle = product.get("handle", "(no handle)")
            status = product.get("status", "(unknown)")
            vendor = product.get("vendor") or "(none)"
            product_type = product.get("productType") or "(none)"
            total_inventory = product.get("totalInventory")
            if total_inventory is None:
                total_inventory = 0

            print("%d. %s" % (index, title))
            print("   handle:           %s" % handle)
            print("   status:           %s" % status)
            print("   vendor:           %s" % vendor)
            print("   product type:     %s" % product_type)
            print("   total inventory:  %s" % total_inventory)

            variant_edges = (product.get("variants") or {}).get("edges") or []
            product_has_missing_sku = False

            if not variant_edges:
                print("   variants:         (none)")
            else:
                print("   variants:")
                for variant_edge in variant_edges:
                    variant = variant_edge.get("node") or {}
                    sku = variant.get("sku") or ""
                    price = variant.get("price", "?")
                    qty = variant.get("inventoryQuantity")
                    if qty is None:
                        qty = 0
                    variant_title = variant.get("title", "(no title)")
                    sku_display = sku if sku else "(missing SKU)"
                    print(
                        "     - %s | SKU: %s | price: %s | inventory: %s"
                        % (variant_title, sku_display, price, qty)
                    )
                    if _variant_sku_missing(sku):
                        product_has_missing_sku = True

            if product_has_missing_sku:
                missing_sku_products.append(title)
            if total_inventory <= 0:
                low_inventory_products.append("%s (total: %s)" % (title, total_inventory))

            print()

    print("-" * 44)
    print("Products returned: %d" % len(edges))
    if missing_sku_products:
        print("Products with missing SKU: %d" % len(missing_sku_products))
        for name in missing_sku_products:
            print("  - %s" % name)
    else:
        print("Products with missing SKU: 0")
    if low_inventory_products:
        print("Products with zero or negative inventory: %d" % len(low_inventory_products))
        for name in low_inventory_products:
            print("  - %s" % name)
    else:
        print("Products with zero or negative inventory: 0")
    print("-" * 44)
    print("SUCCESS: products query completed (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
