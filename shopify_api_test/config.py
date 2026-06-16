import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def get_config():
    store_domain = os.getenv("SHOPIFY_STORE_DOMAIN", "").strip()
    access_token = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "").strip()
    api_version = os.getenv("SHOPIFY_API_VERSION", "2026-01").strip()

    missing = []
    if not store_domain:
        missing.append("SHOPIFY_STORE_DOMAIN")
    if not access_token or access_token == "put_token_here":
        missing.append("SHOPIFY_ADMIN_ACCESS_TOKEN")
    if not api_version:
        missing.append("SHOPIFY_API_VERSION")

    if missing:
        raise ValueError(
            "Missing or placeholder values in .env: %s. "
            "Copy .env.example to .env and fill in your credentials."
            % ", ".join(missing)
        )

    return {
        "store_domain": store_domain,
        "access_token": access_token,
        "api_version": api_version,
        "graphql_url": "https://%s/admin/api/%s/graphql.json"
        % (store_domain, api_version),
    }
