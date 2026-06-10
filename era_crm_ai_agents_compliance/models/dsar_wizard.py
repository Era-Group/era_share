# -*- coding: utf-8 -*-
"""DSAR request wizard — the UI a compliance manager uses to run a PDPL Data
Subject Access Request (access export / erasure) against a contact's consent
records. Delegates to crm.ai.consent.handle_dsar, which runs under the manager's
own permissions (no sudo) and audits the action.
"""
import json

from odoo import fields, models, _


class CrmAiDsarWizard(models.TransientModel):
    _name = "crm.ai.dsar.wizard"
    _description = "CRM AI DSAR Request"

    partner_id = fields.Many2one(
        comodel_name="res.partner", string="Contact", required=True,
    )
    kind = fields.Selection(
        selection=[
            ("access", "Access (export data)"),
            ("erasure", "Erasure (anonymize)"),
        ],
        string="Request Type", required=True, default="access",
    )
    result = fields.Text(string="Result", readonly=True)

    def action_run(self):
        """Run the DSAR and show the outcome, keeping the wizard open."""
        self.ensure_one()
        outcome = self.env["crm.ai.consent"].handle_dsar(self.partner_id, self.kind)
        if self.kind == "access":
            self.result = json.dumps(outcome, ensure_ascii=False, indent=2)
        else:
            self.result = _("Anonymized %s consent record(s) under DSAR erasure.") % outcome
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
