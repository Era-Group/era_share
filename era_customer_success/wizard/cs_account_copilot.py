# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from ..models.cs_account import _cs_md_to_html

_logger = logging.getLogger(__name__)


class CsAccountCopilot(models.TransientModel):
    _name = 'cs.account.copilot'
    _description = 'CS Account Copilot (ask anything about a customer)'

    cs_account_id = fields.Many2one('cs.account', string='Account', required=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    question = fields.Text(string='Question', required=True)
    answer = fields.Html(string='Answer', readonly=True, sanitize=False)

    def _recent_timeline(self, limit=15):
        """Plain-text digest of the last N timeline messages for grounding."""
        self.ensure_one()
        msgs = self.cs_account_id.message_ids.filtered(lambda m: m.body)[:limit]
        lines = []
        for m in msgs:
            txt = html2plaintext(m.body or '').strip().replace('\n', ' ')
            if txt:
                lines.append("- %s: %s" % (m.date, txt[:200]))
        return "\n".join(lines) or "No timeline activity recorded."

    def action_ask(self):
        self.ensure_one()
        self.cs_account_id.check_access('read')
        if self.partner_id != self.cs_account_id.partner_id:
            raise UserError(_('The selected customer does not match the Customer Success account.'))
        if not self.question or not self.question.strip():
            raise UserError(_('Type a question first.'))
        agent = self.env.ref('era_customer_success.cs_account_qa_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The Account Copilot AI agent is not available.'))
        root = self.env.ref('base.user_root')
        prompt = (
            "%s\n\n=== HISTORICAL KPI TREND ===\n%s\n\n=== RECENT TIMELINE ===\n%s\n\n"
            "=== QUESTION ===\n%s" % (
                self.cs_account_id._build_situation_summary(),
                self.cs_account_id._build_snapshot_trend(),
                self._recent_timeline(), self.question.strip()))
        try:
            response = agent.with_user(root).get_direct_response(prompt=prompt)
        except Exception:
            _logger.exception("CS account copilot failed for account %s", self.cs_account_id.id)
            raise UserError(_('Could not get an answer right now. Please try again.'))
        text = (response[0] if response else '') or ''
        if not text.strip():
            raise UserError(_('The AI returned an empty answer. Rephrase and try again.'))
        self.answer = _cs_md_to_html(text.strip())
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Account Copilot'),
        }
