from odoo import _, api, fields, models


class ShopifyTrialPopupWizard(models.TransientModel):
    _name = "shopify.trial.popup.wizard"
    _description = "Shopify Connector Trial Notice"

    message = fields.Html(readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res.setdefault(
            "message",
            _(
                "<p><strong>You are using the Trial version</strong> of the Shopify Connector.</p>"
                "<p>"
                "Trial mode limits export/import capacity. "
                "To get the <strong>complete version</strong> with <strong>unlimited export/import</strong> "
                "and all features enabled, please contact:</p>"
                "<ul>"
                "<li>Email: <strong>gultajkhan980@gmail.com</strong></li>"
                "<li>WhatsApp: <strong>+923151945928</strong></li>"
                "</ul>"
            ),
        )
        return res

    def action_continue(self):
        """Close popup and open the dashboard."""
        self.ensure_one()
        return self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_dashboard"
        )

    def action_contact_whatsapp(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "https://wa.me/923151945928",
            "target": "new",
        }

    def action_contact_email(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "mailto:gultajkhan980@gmail.com",
            "target": "new",
        }


class ShopifyTrialNoticeService(models.AbstractModel):
    """
    Small entry-point helper so menus can route users through a license notice
    before opening the dashboard.
    """

    _name = "shopify.trial.notice.service"
    _description = "Shopify Trial Notice Service"

    @api.model
    def action_open_dashboard_with_trial_notice(self):
        return self.env["ir.actions.act_window"]._for_xml_id(
            "custom_odoo_shopify_connector.action_shopify_dashboard"
        )

