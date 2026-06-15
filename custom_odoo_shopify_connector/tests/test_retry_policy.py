from odoo.tests.common import TransactionCase

from ..services.retry_policy import (
    TRANSIENT_HTTP_STATUS_CODES,
    backoff_seconds,
    classify_exception,
    next_retry_at,
)


class TestRetryPolicy(TransactionCase):
    def test_classify_exception_transient_http(self):
        exc = Exception("Shopify API error 429: Too Many Requests")
        transient, status_code, message = classify_exception(exc)
        self.assertTrue(transient)
        self.assertEqual(status_code, 429)
        self.assertIn("429", message)
        self.assertIn(429, TRANSIENT_HTTP_STATUS_CODES)

    def test_backoff_seconds_increases(self):
        d1 = backoff_seconds(1, base_seconds=2, max_seconds=60, jitter_seconds=0)
        d2 = backoff_seconds(2, base_seconds=2, max_seconds=60, jitter_seconds=0)
        d3 = backoff_seconds(3, base_seconds=2, max_seconds=60, jitter_seconds=0)
        self.assertEqual(d1, 2)
        self.assertEqual(d2, 4)
        self.assertEqual(d3, 8)

    def test_next_retry_at_returns_future(self):
        # We can't freeze time reliably here; just assert ordering with jitter disabled.
        t1 = next_retry_at(1, base_seconds=1, max_seconds=10, jitter_seconds=0)
        t2 = next_retry_at(2, base_seconds=1, max_seconds=10, jitter_seconds=0)
        self.assertTrue(t1)
        self.assertTrue(t2)
        self.assertGreaterEqual(t2, t1)

