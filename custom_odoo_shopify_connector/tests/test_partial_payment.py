from odoo.tests.common import TransactionCase

from ..services.payment_fee_service import PaymentFeeService


class TestPartialPayment(TransactionCase):
    def setUp(self):
        super().setUp()
        self.service = PaymentFeeService(self.env)

    def test_extract_paid_amount_from_transactions(self):
        payload = {
            "financial_status": "partially_paid",
            "total_price": "200.00",
            "transactions": [
                {"id": 1, "kind": "sale", "status": "success", "amount": "80.00"},
                {"id": 2, "kind": "authorization", "status": "success", "amount": "80.00"},
            ],
        }
        self.assertEqual(self.service.extract_paid_amount(payload), 80.0)

    def test_extract_paid_amount_paid_without_transactions(self):
        payload = {
            "financial_status": "paid",
            "total_price": "150.00",
            "transactions": [],
        }
        self.assertEqual(self.service.extract_paid_amount(payload), 150.0)

    def test_extract_sale_transaction_ids(self):
        payload = {
            "transactions": [
                {"id": 11, "kind": "sale", "status": "success", "amount": "50.00"},
                {"id": 12, "kind": "refund", "status": "success", "amount": "10.00"},
            ]
        }
        self.assertEqual(self.service.extract_sale_transaction_ids(payload), ["11"])
