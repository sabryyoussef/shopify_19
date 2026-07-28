# -*- coding: utf-8 -*-
import json

from odoo import fields, models, _
from odoo.exceptions import UserError


class ShopifyPublicCategorySyncWizard(models.TransientModel):
    _name = "shopify.public.category.sync.wizard"
    _description = "Sync Website Categories → Shopify Collections"

    store_id = fields.Many2one(
        "shopify.store",
        required=True,
        default=lambda self: self.env["shopify.store"].search([("active", "=", True)], limit=1),
    )
    dry_run = fields.Boolean(
        string="Dry-run (no Shopify writes)",
        default=True,
    )
    sync_membership = fields.Boolean(
        string="Sync product memberships",
        default=True,
    )
    public_categ_id = fields.Many2one(
        "product.public.category",
        string="Single category (optional)",
        help="Leave empty to sync all website categories.",
    )
    result_json = fields.Text(readonly=True)

    def action_run(self):
        self.ensure_one()
        if not self.store_id:
            raise UserError(_("Select a Shopify store."))
        Service = self.env["shopify.public.category.service"]
        if self.public_categ_id:
            if self.dry_run:
                report = {
                    "plans": [
                        Service.plan_category(self.store_id, self.public_categ_id)
                    ]
                }
                report = Service._summarize_plans(self.store_id, report["plans"])
            else:
                one = Service.sync_category(
                    self.store_id,
                    self.public_categ_id,
                    dry_run=False,
                    sync_membership=self.sync_membership,
                )
                report = one
        else:
            report = Service.sync_all(
                self.store_id,
                dry_run=self.dry_run,
                sync_membership=self.sync_membership,
            )
        self.result_json = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "context": {"form_view_initial_mode": "readonly"},
        }
