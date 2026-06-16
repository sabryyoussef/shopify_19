# Shopify Admin API — read-only test project

Small Python project to validate a Shopify Admin API access token and read basic shop/order data. **No create, update, or delete operations.**

Store: `izone-eg.myshopify.com`

## Prerequisites

- Python 3.9+
- A Shopify Admin API access token with at least:
  - `read_orders` (for the orders test)
  - Shop info uses the default shop read scope available to custom apps

## Setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set your real values:

   ```env
   SHOPIFY_STORE_DOMAIN=izone-eg.myshopify.com
   SHOPIFY_ADMIN_ACCESS_TOKEN=shpat_your_real_token_here
   SHOPIFY_API_VERSION=2026-01
   ```

   Never commit `.env`. It is listed in `.gitignore`.

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Run tests

From this directory (`shopify_api_test/`):

```bash
# 1) Validate token and print shop info
python test_shop_info.py

# 2) Read first 5 orders (read-only)
python test_orders_read.py
```

## Verify success

**Shop info test** should print:

- Shop name
- `myshopifyDomain` (e.g. `izone-eg.myshopify.com`)
- Primary domain host
- `SUCCESS: token is valid and shop info was retrieved.`

**Orders test** should print up to 5 orders with:

- Order name (e.g. `#1001`)
- GraphQL order id
- `createdAt`
- `displayFinancialStatus`
- `totalPriceSet.shopMoney` amount and currency

Exit code `0` means success. Exit code `1` means the script printed `FAILED:` with an error message.

## Common errors

| Symptom | Likely cause | Fix |
|--------|--------------|-----|
| `401 Unauthorized` | Invalid, expired, or revoked token | Regenerate the Admin API access token in Shopify and update `.env` |
| `403 Forbidden` | Token missing scopes (e.g. `read_orders`) | In Shopify admin → Apps → your app → configure Admin API scopes |
| `404 Not Found` | Wrong store domain or API version | Confirm `SHOPIFY_STORE_DOMAIN` is `izone-eg.myshopify.com` and `SHOPIFY_API_VERSION` is supported |
| `Missing or placeholder values in .env` | `.env` not created or token still `put_token_here` | Copy `.env.example` → `.env` and set real values |
| GraphQL `errors` about fields | API version mismatch | Use a supported version such as `2026-01` |

## Security

- Tokens are loaded from `.env` only — never hardcoded in source.
- Scripts do not print the access token.
- All scripts in this folder are read-only GraphQL queries.
- `.env` must stay out of version control.

## Project layout

```
shopify_api_test/
├── .env.example          # Placeholder credentials
├── .gitignore
├── config.py             # Loads env vars
├── shopify_graphql.py    # Shared GraphQL client
├── test_shop_info.py     # Shop validation test
├── test_orders_read.py   # Orders read test
├── requirements.txt
└── README.md
```
