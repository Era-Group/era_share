"""ERA SEO AI — run-level AI actions on the audit run.

Adds two one-click buttons to the audit run form so the admin can fix the
whole run without opening each finding:

  - Suggest Fixes (AI)  -> action_ai_suggest_findings
  - Auto-Fix (>=0.8)     -> action_ai_fix_findings (suggest + apply confident)

Both operate only on the run's AI-fixable, unresolved findings.
"""
from odoo import _, api, fields, models


class EraSeoAuditRun(models.Model):
    _inherit = 'era.seo.audit.run'

    ai_fixable_count = fields.Integer(
        string='AI-Fixable Findings',
        compute='_compute_ai_fixable_count',
    )

    @api.depends('finding_ids.ai_supported', 'finding_ids.is_resolved')
    def _compute_ai_fixable_count(self):
        for run in self:
            run.ai_fixable_count = len(run._ai_fixable_findings())

    def _ai_fixable_findings(self):
        self.ensure_one()
        return self.finding_ids.filtered(
            lambda f: f.ai_supported and not f.is_resolved
        )

    def action_ai_suggest_findings(self):
        """Generate AI suggestions for every AI-fixable finding in this run."""
        self.ensure_one()
        targets = self._ai_fixable_findings()
        if not targets:
            return self._ai_run_notify('info', _('No AI-fixable findings in this run.'))
        return targets.action_ai_suggest()

    def action_ai_fix_findings(self):
        """Suggest + auto-apply (confidence >= 0.8) across the run's findings."""
        self.ensure_one()
        targets = self._ai_fixable_findings()
        if not targets:
            return self._ai_run_notify('info', _('No AI-fixable findings in this run.'))
        return targets.action_ai_suggest_and_apply()

    @staticmethod
    def _ai_run_notify(kind, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': kind,
                'message': message,
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
