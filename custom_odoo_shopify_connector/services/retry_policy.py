import random
import re
from datetime import timedelta

from odoo import fields


TRANSIENT_HTTP_STATUS_CODES = {408, 409, 423, 425, 429, 500, 502, 503, 504}
_HTTP_ERROR_RE = re.compile(r"Shopify API error\s+(\d+)\s*:")


def classify_exception(exc):
    """Return transient flag + parsed metadata for an exception."""
    message = str(exc or "")
    status_code = None
    match = _HTTP_ERROR_RE.search(message)
    if match:
        try:
            status_code = int(match.group(1))
        except Exception:
            status_code = None

    lowered = message.lower()
    transient_by_message = any(
        token in lowered
        for token in (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection refused",
            "try again",
            "rate limit",
            "too many requests",
            "proxy error",
            "bad gateway",
            "gateway timeout",
            "service unavailable",
        )
    )
    transient = (
        status_code in TRANSIENT_HTTP_STATUS_CODES
        or transient_by_message
    )
    return transient, status_code, message


def backoff_seconds(attempt, base_seconds=30, max_seconds=1800, jitter_seconds=10):
    """Exponential backoff with bounded jitter."""
    safe_attempt = max(1, int(attempt or 1))
    delay = min(max_seconds, base_seconds * (2 ** (safe_attempt - 1)))
    if jitter_seconds:
        delay += random.randint(0, jitter_seconds)
    return int(delay)


def next_retry_at(attempt, base_seconds=30, max_seconds=1800, jitter_seconds=10):
    """Return Odoo datetime for next retry."""
    delay = backoff_seconds(
        attempt=attempt,
        base_seconds=base_seconds,
        max_seconds=max_seconds,
        jitter_seconds=jitter_seconds,
    )
    return fields.Datetime.now() + timedelta(seconds=delay)

