# -*- coding: utf-8 -*-
"""Opt-out surface on the partner.

Adds the request timestamp the 72h SLA is measured from, and thin wrappers that
let the ir.cron and any UI action reach the opt-out service functions (a model
method is required because ir.cron can only call registry models, not plain
service classes). The Base already provides crm_ai_intl_processing_consent /
crm_ai_consent_date; this only adds the opt-out request time on top.
"""
from odoo import api, fields, models, _


class ResPartner(models.Model):
    _inherit = "res.partner"

    crm_ai_opt_out_requested_on = fields.Datetime(
        string="AI Opt-out Requested On",
        help="When this contact requested to opt out of AI marketing messages. "
             "The PDPL 72-hour enforcement window is measured from here; the "
             "daily safety-net cron guarantees the withdrawal is applied.",
    )
    crm_ai_consent_ids = fields.One2many(
        comodel_name="crm.ai.consent",
        inverse_name="partner_id",
        string="AI Consent Log",
    )

    def action_crm_ai_opt_out(self):
        """Manager UI button: apply an opt-out to the selected contact(s)."""
        self.process_opt_out(source="manual-ui")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Opt-out applied"),
                "message": _("Marketing consent withdrawn for %s contact(s).") % len(self),
                "sticky": False,
            },
        }

    def action_crm_ai_open_dsar(self):
        """Manager UI button: open the DSAR wizard preset to this contact."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.ai.dsar.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id},
        }

    def process_opt_out(self, source="manual"):
        """Apply an opt-out to every partner in the recordset (delegates to the
        opt-out service)."""
        from ..services import opt_out
        for partner in self:
            opt_out.process_opt_out(self.env, partner, source=source)
        return True

    @api.model
    def cron_enforce_72h(self):
        """ir.cron entry point — daily 72h opt-out safety net."""
        from ..services import opt_out
        return opt_out.cron_enforce_72h(self.env)
