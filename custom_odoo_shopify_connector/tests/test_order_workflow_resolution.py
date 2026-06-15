from odoo.tests.common import TransactionCase

from ..services.order_import_service import OrderImportService


class TestOrderWorkflowResolution(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Store = self.env["shopify.store"].sudo()
        self.Gateway = self.env["shopify.payment.gateway"].sudo()
        self.Workflow = self.env["shopify.sale.auto.workflow"].sudo()
        self.store = self.Store.create(
            {
                "name": "Workflow Test Store",
                "shop_url": "https://workflow-test.myshopify.com",
                "access_token": "dummy",
                "active": True,
                "webhook_secret": "secret",
            }
        )
        self.service = OrderImportService(self.env)

    def test_get_financial_workflow_uses_any_workflow_when_no_mapping(self):
        fallback_workflow = self.Workflow.create({"name": "Fallback Any Workflow"})
        self.Gateway.create(
            {
                "name": "Manual Gateway",
                "instance_id": self.store.id,
                "payment_code": "manual",
                "active": True,
            }
        )
        payload = {
            "id": "SO-1001",
            "payment_gateway_names": ["manual"],
            "financial_status": "partially_paid",
        }

        workflow = self.service.get_financial_workflow(self.store, payload)
        self.assertEqual(workflow.id, fallback_workflow.id)
