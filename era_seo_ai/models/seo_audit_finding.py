"""ERA SEO AI — extend era.seo.audit.finding with the AI auto-fix workflow.

Two-step UX:

  1. **Suggest Fix** — calls Claude, stores the proposal on the finding.
     The admin reviews proposed_value / explanation / confidence.
  2. **Apply Fix** — writes the proposal to the target record and marks
     the finding resolved.

Per CLAUDE.md §19: admin actions must be server-side gated. The two
buttons check ``group_era_seo_manager`` before doing anything destructive;
"Apply" also re-resolves the target record to guard against the finding's
res_id pointing at a deleted page (auto-fix on a vanished record would
crash silently otherwise).
"""
import json
import logging

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from .ai_client import AIClient, AIUnavailable

_logger = logging.getLogger(__name__)


AI_STATUS = [
    ('none', 'No Suggestion'),
    ('suggested', 'Suggested'),
    ('applied', 'Applied'),
    ('failed', 'Failed'),
    ('not_supported', 'Not AI-Fixable'),
]

# Checks the AI workflow can handle. Findings with other check codes get
# ai_status = 'not_supported' so the UI hides the button.
AI_FIXABLE_CODES = {
    'missing_seo_title',
    'missing_meta_description',
    'title_too_long',
    'title_too_short',
    'description_too_long',
    'description_too_short',
    'slug_contains_uppercase',
    'slug_contains_stopwords',
    'slug_too_long',
}


class EraSeoAuditFinding(models.Model):
    _inherit = 'era.seo.audit.finding'

    ai_status = fields.Selection(
        AI_STATUS,
        default='none',
        readonly=True,
        index=True,
    )
    ai_proposed_value = fields.Text(
        string='AI Proposed Value', readonly=True,
        help='The proposed value in the website default language (shown here '
             'for review). Per-language values are in AI Proposed Translations.',
    )
    ai_proposed_translations = fields.Text(
        string='AI Proposed Translations', readonly=True,
        help='JSON map of {lang_code: value}. Apply writes each into the '
             'matching language translation of the target field. Empty for the '
             'non-translatable slug.',
    )
    ai_proposed_field = fields.Char(string='Target Field', readonly=True)
    ai_explanation = fields.Text(string='AI Explanation', readonly=True)
    ai_confidence = fields.Float(
        string='AI Confidence',
        digits=(3, 2),
        readonly=True,
        help='Self-reported confidence (0.0-1.0). Below 0.7 deserves a manual review.',
    )
    ai_model_used = fields.Char(string='Model', readonly=True)
    ai_last_log_id = fields.Many2one('era.seo.ai.fix.log', readonly=True)
    ai_supported = fields.Boolean(
        string='AI-Fixable',
        compute='_compute_ai_supported',
        store=True,
    )

    def _compute_ai_supported(self):
        for rec in self:
            rec.ai_supported = rec.check_code in AI_FIXABLE_CODES

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_ai_suggest(self):
        """Call Claude for each selected finding; store proposals."""
        self._check_manager()
        Log = self.env['era.seo.ai.fix.log'].sudo()
        client = AIClient(self.env)

        ok, reason = client.is_available()
        if not ok:
            raise UserError(reason)

        errors = []
        for rec in self:
            if rec.check_code not in AI_FIXABLE_CODES:
                rec.write({'ai_status': 'not_supported'})
                continue
            target = rec._ai_resolve_target()
            if not target:
                errors.append(_('Finding %s: target record vanished.', rec.id))
                continue
            try:
                proposal = client.suggest_fix(rec, target)
            except AIUnavailable as exc:
                errors.append(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                _logger.exception('AI suggest_fix failed for finding %d', rec.id)
                log = Log.create({
                    'finding_id': rec.id,
                    'check_code': rec.check_code,
                    'target_model': rec.res_model,
                    'target_id': rec.res_id,
                    'target_url': rec.url,
                    'error_message': str(exc),
                })
                rec.write({'ai_status': 'failed', 'ai_last_log_id': log.id})
                errors.append(_('Finding %s: %s', rec.id, exc))
                continue

            translations = proposal.get('translations') or {}
            log = Log.create({
                'finding_id': rec.id,
                'check_code': rec.check_code,
                'target_model': rec.res_model,
                'target_id': rec.res_id,
                'target_url': rec.url,
                'model': proposal['model'],
                'field_written': '{} ({})'.format(
                    proposal['field'],
                    ', '.join(translations) if translations else 'single',
                ),
                'proposed_value': (
                    json.dumps(translations, ensure_ascii=False, indent=2)
                    if translations else proposal['proposed_value']
                ),
                'explanation': proposal['explanation'],
                'confidence': proposal['confidence'],
            })
            rec.write({
                'ai_status': 'suggested',
                'ai_proposed_value': proposal['proposed_value'],
                'ai_proposed_translations': (
                    json.dumps(translations, ensure_ascii=False) if translations else False
                ),
                'ai_proposed_field': proposal['field'],
                'ai_explanation': proposal['explanation'],
                'ai_confidence': proposal['confidence'],
                'ai_model_used': proposal['model'],
                'ai_last_log_id': log.id,
            })

        if errors:
            return self._notify(
                'warning',
                _('Suggested with %d error(s):\n%s', len(errors), '\n'.join(errors[:5])),
            )
        return self._notify('success', _('AI suggestions ready for review.'))

    def action_ai_apply(self):
        """Write the stored proposal to the target record."""
        self._check_manager()
        Log = self.env['era.seo.ai.fix.log'].sudo()
        applied = 0
        errors = []
        for rec in self:
            if rec.ai_status != 'suggested':
                continue
            if not rec.ai_proposed_field or rec.ai_proposed_value is None:
                continue
            target = rec._ai_resolve_target()
            if not target:
                errors.append(_('Finding %s: target gone.', rec.id))
                continue
            try:
                translations = {}
                if rec.ai_proposed_translations:
                    translations = json.loads(rec.ai_proposed_translations)
                if translations:
                    # Translatable field — write each language's value.
                    for lang_code, value in translations.items():
                        target.sudo().with_context(lang=lang_code).write(
                            {rec.ai_proposed_field: value})
                else:
                    # Non-translatable (slug) or single-value proposal.
                    target.sudo().write({rec.ai_proposed_field: rec.ai_proposed_value})
            except Exception as exc:  # noqa: BLE001
                errors.append(_('Finding %s: %s', rec.id, exc))
                continue
            rec.write({
                'ai_status': 'applied',
                'is_resolved': True,
                'resolved_date': fields.Datetime.now(),
                'resolved_user_id': self.env.user.id,
            })
            if rec.ai_last_log_id:
                rec.ai_last_log_id.sudo().write({
                    'applied': True,
                    'applied_date': fields.Datetime.now(),
                    'applied_user_id': self.env.user.id,
                })
            applied += 1

        if errors:
            return self._notify(
                'warning',
                _('Applied %d, failed %d.\n%s', applied, len(errors),
                  '\n'.join(errors[:5])),
            )
        return self._notify('success', _('%d fix(es) applied.', applied))

    def action_ai_suggest_and_apply(self):
        """Convenience: suggest then immediately apply for high-confidence proposals."""
        self.action_ai_suggest()
        # Auto-apply only the high-confidence ones; admin reviews the rest.
        threshold = 0.8
        to_apply = self.filtered(
            lambda f: f.ai_status == 'suggested' and f.ai_confidence >= threshold
        )
        return to_apply.action_ai_apply()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ai_resolve_target(self):
        self.ensure_one()
        if not (self.res_model and self.res_id):
            return False
        if self.res_model not in self.env:
            return False
        target = self.env[self.res_model].sudo().browse(self.res_id)
        return target if target.exists() else False

    def _check_manager(self):
        if not self.env.user.has_group('era_seo_manager.group_era_seo_manager'):
            raise AccessError(
                _('AI auto-fix actions require the SEO Manager group.')
            )

    @staticmethod
    def _notify(kind, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': kind,  # 'success' | 'warning' | 'danger' | 'info'
                'message': message,
                'sticky': kind == 'warning',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
