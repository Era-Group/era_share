"""ERA SEO AI — run-level AI actions on the audit run.

Adds two one-click buttons to the audit run form so the admin can fix the
whole run without opening each finding:

  - Suggest Fixes (AI)  -> action_ai_suggest_findings
  - Auto-Fix (>=0.8)     -> action_ai_fix_findings (suggest + apply confident)

Both operate only on the run's AI-fixable, unresolved findings.

Auto-Fix runs in the BACKGROUND: a run with many findings makes one slow AI
call per finding and would run the request past the worker's limit_time_real
(1200s), killing it. The button just flips an ICP flag and fires
era.seo.suite.hub.cron_bulk_ai_fix, which drains the queue out of the request.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_TRUE = ('True', '1', 'true', 'yes', 'on')


class EraSeoAuditRun(models.Model):
    _inherit = 'era.seo.audit.run'

    ai_fixable_count = fields.Integer(
        string='AI-Fixable Findings',
        compute='_compute_ai_fixable_count',
    )
    ai_failed_count = fields.Integer(
        string='Failed Findings',
        compute='_compute_ai_failed_count',
        help='Findings whose AI suggest_fix raised an error last time '
             '(usually a provider-config or transient network issue). '
             'Use Retry Failed to flip them back to "none" so the next '
             'Suggest pass picks them up again.',
    )

    @api.depends('finding_ids.ai_supported', 'finding_ids.is_resolved')
    def _compute_ai_fixable_count(self):
        for run in self:
            run.ai_fixable_count = len(run._ai_fixable_findings())

    @api.depends('finding_ids.ai_status')
    def _compute_ai_failed_count(self):
        for run in self:
            run.ai_failed_count = len(
                run.finding_ids.filtered(lambda f: f.ai_status == 'failed'))

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
        """Queue a BACKGROUND suggest + auto-apply (>=0.8) sweep for this run.

        Does not do the AI work in-request — see the module docstring. Flips
        `era_seo.bulk_fix_active`, records this run, and fires the drain cron.
        Re-entrancy: if a sweep is already active we reject the click rather
        than enqueue a second one (the cron framework also can't run two ticks
        of the same cron at once).
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        if (ICP.get_param('era_seo.bulk_fix_active') or '') in _TRUE:
            return self._ai_run_notify(
                'warning',
                _('An AI auto-fix sweep is already running — let it finish '
                  'before starting another.'))
        # Only the untouched findings are queued; the cron processes
        # ai_status == 'none'. Failed ones are re-queued via "Retry Failed".
        targets = self._ai_fixable_findings().filtered(
            lambda f: f.ai_status == 'none')
        if not targets:
            return self._ai_run_notify(
                'info',
                _('No new AI-fixable findings to process in this run. '
                  '(Use "Retry Failed" to re-queue failed ones.)'))
        ICP.set_param('era_seo.bulk_fix_run_id', str(self.id))
        ICP.set_param('era_seo.bulk_fix_last_id', '0')
        ICP.set_param('era_seo.bulk_fix_active', 'True')
        cron = self.env.ref(
            'era_seo_suite.cron_bulk_ai_fix', raise_if_not_found=False)
        if cron:
            try:
                cron.sudo()._trigger()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    'action_ai_fix_findings: cron _trigger failed')
        return self._ai_run_notify(
            'success',
            _('AI auto-fix started in the background for %d finding(s). '
              'They update as each is fixed — you can keep working.',
              len(targets)))

    def action_ai_retry_failed_findings(self):
        """Reset every `failed` finding in this run back to `none`.

        Surfaces the per-finding reset on the run header so an admin can
        retry every failed row in one click — after fixing the upstream
        provider config (API key, agent picked, etc.) — without
        re-running the audit from scratch.
        """
        self.ensure_one()
        targets = self.finding_ids.filtered(lambda f: f.ai_status == 'failed')
        if not targets:
            return self._ai_run_notify(
                'info', _('No failed findings in this run.'))
        return targets.action_ai_retry_failed()

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
