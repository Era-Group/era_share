# -*- coding: utf-8 -*-
import logging

from odoo import fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CsCallBriefing(models.TransientModel):
    _name = 'cs.call.briefing'
    _description = 'AI Pre-Call / Meeting Briefing'

    cs_account_id = fields.Many2one('cs.account', string='Account', required=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    purpose = fields.Selection([
        ('call', 'Call'),
        ('meeting', 'Meeting'),
        ('renewal', 'Renewal conversation'),
        ('recovery', 'Service recovery'),
    ], string='Context', default='call', required=True)
    briefing = fields.Text(string='Briefing', readonly=True)

    def _generate(self):
        self.ensure_one()
        self.cs_account_id.check_access('read')
        if self.partner_id != self.cs_account_id.partner_id:
            raise UserError(_('The selected customer does not match the Customer Success account.'))
        agent = self.env.ref('era_customer_success.cs_call_prep_agent',
                             raise_if_not_found=False)
        if not agent:
            raise UserError(_('The call-briefing AI agent is not available.'))
        root = self.env.ref('base.user_root')
        purpose_label = dict(self._fields['purpose'].selection).get(self.purpose)
        prompt = "%s\n\n=== HISTORICAL KPI TREND ===\n%s\n\nUpcoming contact type: %s. Prepare the briefing." % (
            self.cs_account_id._build_situation_summary(),
            self.cs_account_id._build_snapshot_trend(), purpose_label)
        try:
            response = agent.with_user(root).get_direct_response(prompt=prompt)
        except Exception:
            _logger.exception("CS call briefing failed for account %s", self.cs_account_id.id)
            raise UserError(_('Could not generate a briefing right now. Please try again.'))
        text = (response[0] if response else '') or ''
        if not text.strip():
            raise UserError(_('The AI returned an empty briefing. Please try again.'))
        self.briefing = text.strip()

    def action_regenerate(self):
        self.ensure_one()
        self._generate()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Pre-Call Briefing (AI)'),
        }
