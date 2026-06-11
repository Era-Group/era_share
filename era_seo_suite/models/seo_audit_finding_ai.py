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

from odoo import _, api, fields, models, modules
from odoo.exceptions import AccessError, UserError

from .ai_client import AIClient, AIUnavailable

_logger = logging.getLogger(__name__)


AI_STATUS = [
    ('none', 'No Suggestion'),
    ('suggested', 'Suggested'),
    ('applied', 'Applied'),
    ('failed', 'Failed'),
    ('manual_review', 'Needs Review'),
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
    # Richer fixes (19.0.7.0.0):
    'missing_og_image',     # mechanical: company logo
    'missing_schema',       # AI picks a JSON-LD template, attaches an instance
    'image_missing_alt',    # AI writes alt text, injected into the content imgs
    'thin_content',         # AI proposes an HTML block, appended on apply
}

# Even if the configured threshold is lowered, these fix families must never
# be applied without a human clicking Apply. Thin-content writes AI-authored
# page body copy; keeping it review-only preserves editorial control.
AI_NEVER_AUTO_APPLY_FIX_TYPES = {'thin_content'}


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
    ai_fix_type = fields.Char(
        string='Fix Type', readonly=True,
        help="How Apply writes the proposal: 'field' (write a field value), "
             "'og_image' (set the OG image to the company logo), 'schema' "
             "(attach a JSON-LD schema instance), 'image_alt' (inject alt text "
             "into the content images), or 'thin_content' (append an HTML block).",
    )
    ai_fix_payload = fields.Text(
        string='Fix Payload (JSON)', readonly=True,
        help='Structured data for non-field fixes (schema template code, image '
             'alt pairs, expansion HTML). Empty for plain field fixes.',
    )
    ai_explanation = fields.Text(string='AI Explanation', readonly=True)
    ai_confidence = fields.Float(
        string='AI Confidence',
        digits=(3, 2),
        readonly=True,
        help='Self-reported confidence (0.0-1.0). Below 0.7 deserves a manual review.',
    )
    ai_model_used = fields.Char(string='Model', readonly=True)
    ai_last_log_id = fields.Many2one('era.seo.ai.fix.log', readonly=True)
    ai_attempt_count = fields.Integer(
        string='AI Attempts',
        readonly=True,
        default=0,
        help='Number of automated or manual Suggest Fix attempts made for '
             'this persistent finding. Used by Autopilot to avoid spending '
             'repeated AI calls on the same unresolved issue.',
    )
    ai_last_attempt_date = fields.Datetime(
        string='Last AI Attempt',
        readonly=True,
    )
    ai_needs_manual_review = fields.Boolean(
        string='Needs Manual Review',
        readonly=True,
        index=True,
        help='Set when Autopilot produced a low-confidence proposal, hit the '
             'attempt limit, could not apply the proposal, or found a case '
             'that should be decided by a person.',
    )
    ai_review_reason = fields.Text(
        string='Manual Review Reason',
        readonly=True,
    )
    ai_supported = fields.Boolean(
        string='AI-Fixable',
        compute='_compute_ai_supported',
        # Intentionally NOT stored: AI_FIXABLE_CODES is a Python constant that
        # grows between versions. A stored value computed before a code became
        # fixable would stay stale (the compute has nothing to re-trigger it),
        # leaving findings "not AI-fixable" forever and hiding the run's AI
        # buttons. Evaluating on read keeps it correct after every upgrade.
    )

    @api.depends('check_code')
    def _compute_ai_supported(self):
        for rec in self:
            rec.ai_supported = rec.check_code in AI_FIXABLE_CODES

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_ai_suggest(self):
        """Call the configured Odoo AI agent for each finding; store proposals."""
        self._check_manager()
        Log = self.env['era.seo.ai.fix.log'].sudo()
        client = AIClient(self.env)

        ok, reason = client.is_available()
        if not ok:
            raise UserError(reason)

        errors = []
        # When the underlying AI provider is misconfigured (no API key,
        # rate limit, unreachable) the FIRST call fails with a UserError
        # and every following call in the loop will fail identically. We
        # detect that case and short-circuit the rest of the batch with
        # one summary log line instead of emitting N identical stacktraces.
        provider_error = None
        for rec in self:
            if rec.check_code not in AI_FIXABLE_CODES:
                rec.write({'ai_status': 'not_supported'})
                continue
            target = rec._ai_resolve_target()
            if not target:
                rec._ai_mark_manual_review(
                    _('Target record no longer exists; inspect the finding manually.'),
                    status='manual_review',
                )
                errors.append(_('Finding %s: target record vanished.', rec.id))
                continue
            if provider_error is not None:
                # Already saw a provider-config failure this batch. Mark
                # the remaining findings as failed without re-calling the
                # provider — the next sweep will retry after the admin
                # fixes the configuration.
                rec.write({
                    'ai_status': 'failed',
                    'ai_needs_manual_review': True,
                    'ai_review_reason': provider_error,
                })
                continue
            if rec._ai_system_attempt_limit_reached():
                reason = rec._ai_attempt_limit_message()
                log = Log.create({
                    'finding_id': rec.id,
                    'check_code': rec.check_code,
                    'target_model': rec.res_model,
                    'target_id': rec.res_id,
                    'target_url': rec.url,
                    'error_message': reason,
                    'autopilot_decision': 'blocked',
                    'autopilot_reason': reason,
                })
                rec.write({'ai_last_log_id': log.id})
                rec._ai_mark_manual_review(reason, status='manual_review')
                continue
            try:
                rec._ai_register_attempt()
                proposal = client.suggest_fix(rec, target)
            except AIUnavailable as exc:
                rec.write({
                    'ai_status': 'failed',
                    'ai_needs_manual_review': True,
                    'ai_review_reason': str(exc),
                })
                errors.append(str(exc))
                continue
            except UserError as exc:
                # Typically a provider-config issue: "No API key set for
                # provider 'X'", "Quota exceeded", etc. One WARNING per
                # batch (not per finding) and one stored log row.
                provider_error = str(exc)
                _logger.warning(
                    'AI suggest_fix: provider unavailable (%s) — '
                    'skipping remaining findings this batch', provider_error)
                log = Log.create({
                    'finding_id': rec.id,
                    'check_code': rec.check_code,
                    'target_model': rec.res_model,
                    'target_id': rec.res_id,
                    'target_url': rec.url,
                    'error_message': provider_error,
                    'autopilot_decision': (
                        'failed' if rec.env.context.get('_era_ai_system') else False
                    ),
                    'autopilot_reason': provider_error,
                })
                rec.write({
                    'ai_status': 'failed',
                    'ai_last_log_id': log.id,
                    'ai_needs_manual_review': True,
                    'ai_review_reason': provider_error,
                })
                errors.append(_('Finding %s: %s', rec.id, exc))
                continue
            except Exception as exc:  # noqa: BLE001
                _logger.exception('AI suggest_fix failed for finding %d', rec.id)
                reason = str(exc)
                log = Log.create({
                    'finding_id': rec.id,
                    'check_code': rec.check_code,
                    'target_model': rec.res_model,
                    'target_id': rec.res_id,
                    'target_url': rec.url,
                    'error_message': reason,
                    'autopilot_decision': (
                        'failed' if rec.env.context.get('_era_ai_system') else False
                    ),
                    'autopilot_reason': reason,
                })
                rec.write({
                    'ai_status': 'failed',
                    'ai_last_log_id': log.id,
                    'ai_needs_manual_review': True,
                    'ai_review_reason': reason,
                })
                errors.append(_('Finding %s: %s', rec.id, exc))
                continue

            translations = proposal.get('translations') or {}
            fix_type = proposal.get('fix_type') or 'field'
            payload = proposal.get('payload') or {}
            if fix_type == 'field':
                field_written = '{} ({})'.format(
                    proposal['field'],
                    ', '.join(translations) if translations else 'single',
                )
            else:
                field_written = fix_type
            log = Log.create({
                'finding_id': rec.id,
                'check_code': rec.check_code,
                'target_model': rec.res_model,
                'target_id': rec.res_id,
                'target_url': rec.url,
                'model': proposal['model'],
                'field_written': field_written,
                'proposed_value': (
                    json.dumps(translations, ensure_ascii=False, indent=2)
                    if translations else proposal['proposed_value']
                ),
                'explanation': proposal['explanation'],
                'confidence': proposal['confidence'],
                'autopilot_decision': (
                    'suggested' if rec.env.context.get('_era_ai_system') else False
                ),
            })
            rec.write({
                'ai_status': 'suggested',
                'ai_proposed_value': proposal['proposed_value'],
                'ai_proposed_translations': (
                    json.dumps(translations, ensure_ascii=False) if translations else False
                ),
                'ai_proposed_field': proposal['field'] or False,
                'ai_fix_type': fix_type,
                'ai_fix_payload': (
                    json.dumps(payload, ensure_ascii=False) if payload else False
                ),
                'ai_explanation': proposal['explanation'],
                'ai_confidence': proposal['confidence'],
                'ai_model_used': proposal['model'],
                'ai_last_log_id': log.id,
                'ai_needs_manual_review': False,
                'ai_review_reason': False,
            })
            # Persist each suggestion as it lands. A run with many findings
            # makes one (slow) AI call per record, so the loop can outlast the
            # web/proxy timeout; without an incremental commit the whole batch
            # — every AI call already paid for — would roll back. Committing
            # per record makes the work durable and lets a re-run resume.
            # Never commit inside a test transaction (forbidden by the test
            # framework — same guard core uses, e.g. mail_mail).
            if not modules.module.current_test:
                self.env.cr.commit()

        if errors:
            return self._notify(
                'warning',
                _('Suggested with %d error(s):\n%s', len(errors), '\n'.join(errors[:5])),
            )
        return self._notify('success', _('AI suggestions ready for review.'))

    def action_ai_apply(self):
        """Write the stored proposal to the target record."""
        self._check_manager()
        applied = 0
        errors = []
        for rec in self:
            if rec.ai_status != 'suggested':
                continue
            target = rec._ai_resolve_target()
            if not target:
                rec._ai_mark_manual_review(
                    _('Target record no longer exists; inspect the finding manually.'),
                    status='manual_review',
                )
                errors.append(_('Finding %s: target gone.', rec.id))
                continue
            try:
                # Savepoint per finding: a DB-level failure (arch XML
                # validation, a schema constraint) rolls back only this
                # record so the rest of the batch still applies.
                with self.env.cr.savepoint():
                    rec._ai_apply_to_target(target)
                    rec.write({
                        'ai_status': 'applied',
                        'is_resolved': True,
                        'resolved_date': fields.Datetime.now(),
                        'resolved_user_id': self.env.user.id,
                        'ai_needs_manual_review': False,
                        'ai_review_reason': False,
                    })
                    if rec.ai_last_log_id:
                        rec.ai_last_log_id.sudo().write({
                            'applied': True,
                            'applied_date': fields.Datetime.now(),
                            'applied_user_id': self.env.user.id,
                            'autopilot_decision': (
                                'auto_applied'
                                if rec.env.context.get('_era_ai_system')
                                else rec.ai_last_log_id.autopilot_decision
                            ),
                        })
            except Exception as exc:  # noqa: BLE001
                reason = str(exc)
                rec._ai_mark_manual_review(reason, status='suggested')
                if rec.ai_last_log_id:
                    rec.ai_last_log_id.sudo().write({
                        'error_message': reason,
                        'autopilot_decision': (
                            'failed' if rec.env.context.get('_era_ai_system')
                            else rec.ai_last_log_id.autopilot_decision
                        ),
                        'autopilot_reason': reason,
                    })
                errors.append(_('Finding %s: %s', rec.id, exc))
                continue
            applied += 1
            # Commit each applied fix on its own (same rationale as
            # action_ai_suggest): a timeout mid-batch can't undo the fixes
            # already written to their target records. Never commit inside
            # a test transaction (forbidden by the test framework).
            if not modules.module.current_test:
                self.env.cr.commit()

        if errors:
            return self._notify(
                'warning',
                _('Applied %d, failed %d.\n%s', applied, len(errors),
                  '\n'.join(errors[:5])),
            )
        return self._notify('success', _('%d fix(es) applied.', applied))

    def action_ai_suggest_and_apply(self):
        """Suggest + auto-apply above the configured confidence threshold.

        Processed per record — not suggest-the-whole-run-then-apply-the-whole-
        run — so each finding is suggested, applied and committed before the
        next begins (the commits live in action_ai_suggest / action_ai_apply).
        The slow part is the per-finding AI call, so a big run easily outlasts
        the web/proxy timeout; the old two-phase version did every AI call
        first and applied nothing until the very end, so a timeout discarded
        the entire run. Per-record means a timeout leaves every finding handled
        so far fully fixed and saved, and the next click resumes with the rest.
        """
        threshold = self._ai_confidence_threshold()
        applied = 0
        for rec in self:
            # Singleton calls reuse the existing per-record handling (provider
            # errors, savepoints) and commit as they go.
            rec.action_ai_suggest()
            if rec.ai_status != 'suggested':
                continue
            if rec._ai_can_auto_apply(threshold):
                rec.action_ai_apply()
                if rec.ai_status == 'applied':
                    applied += 1
            else:
                reason = rec._ai_auto_review_reason(threshold)
                rec._ai_mark_manual_review(reason, status='suggested')
                if rec.ai_last_log_id:
                    rec.ai_last_log_id.sudo().write({
                        'autopilot_decision': 'review',
                        'autopilot_reason': reason,
                    })
        return self._notify('success', _('%d fix(es) applied.', applied))

    def action_ai_retry_failed(self):
        """Reset failed/review-blocked findings so AI can pick them up again.

        Background: when the AI provider is temporarily down or
        misconfigured (no key, ReadTimeout, quota), the per-finding
        handler in action_ai_suggest catches the UserError, marks the
        finding `ai_status = 'failed'`, and moves on. The weekly cron's
        bulk path then filters for ai_status == 'none' and never retries
        the failed ones — so once you've fixed the provider config,
        you'd be stuck having to delete + re-run an audit to retry.

        This action flips failed/manual-review states back to `none`, clears
        the stored proposal, and resets the attempt counter so the next
        Suggest pass starts from a clean slate. Honors the SEO Manager group
        check.
        """
        self._check_manager()
        failed = self.filtered(
            lambda f: f.ai_status in ('failed', 'manual_review')
            or f.ai_needs_manual_review
        )
        if not failed:
            return self._notify(
                'info', _('No failed or review-blocked findings in the selection.'))
        failed.write({
            'ai_status': 'none',
            'ai_proposed_value': False,
            'ai_proposed_translations': False,
            'ai_proposed_field': False,
            'ai_fix_type': False,
            'ai_fix_payload': False,
            'ai_explanation': False,
            'ai_confidence': 0.0,
            'ai_model_used': False,
            'ai_attempt_count': 0,
            'ai_last_attempt_date': False,
            'ai_needs_manual_review': False,
            'ai_review_reason': False,
        })
        return self._notify(
            'success',
            _('Reset %d finding(s) — they will be picked up on the '
              'next Suggest Fixes run.', len(failed)),
        )

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

    def _ai_register_attempt(self):
        """Record one Suggest attempt before the provider/mechanical handler.

        The value is stored on the persistent finding, not the transient run,
        so repeated audits do not reset the spend guard for the same defect.
        """
        self.ensure_one()
        self.sudo().write({
            'ai_attempt_count': (self.ai_attempt_count or 0) + 1,
            'ai_last_attempt_date': fields.Datetime.now(),
        })

    def _ai_max_attempts(self):
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            value = int(ICP.get_param('era_seo.autopilot_max_attempts', '2'))
        except (TypeError, ValueError):
            value = 2
        return max(1, value)

    def _ai_confidence_threshold(self):
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            pct = int(ICP.get_param('era_seo.autopilot_confidence_pct', '80'))
        except (TypeError, ValueError):
            pct = 80
        pct = max(0, min(100, pct))
        return pct / 100.0

    def _ai_system_attempt_limit_reached(self):
        self.ensure_one()
        if not self.env.context.get('_era_ai_system'):
            return False
        return (self.ai_attempt_count or 0) >= self._ai_max_attempts()

    def _ai_attempt_limit_message(self):
        self.ensure_one()
        return _(
            'Autopilot stopped after %(attempts)d attempt(s) on this same '
            'finding. Review it manually, or use Retry Failed to reset the '
            'attempt budget after changing the AI/provider setup.',
            attempts=self.ai_attempt_count or 0,
        )

    def _ai_can_auto_apply(self, threshold):
        self.ensure_one()
        if self.ai_status != 'suggested':
            return False
        if (self.ai_fix_type or 'field') in AI_NEVER_AUTO_APPLY_FIX_TYPES:
            return False
        return (self.ai_confidence or 0.0) >= threshold

    def _ai_auto_review_reason(self, threshold):
        self.ensure_one()
        fix_type = self.ai_fix_type or 'field'
        if fix_type in AI_NEVER_AUTO_APPLY_FIX_TYPES:
            return _(
                'Autopilot kept this proposal for review because "%s" changes '
                'page body content.', fix_type)
        return _(
            'AI confidence %.2f is below the automatic apply threshold %.2f.',
            self.ai_confidence or 0.0, threshold)

    def _ai_mark_manual_review(self, reason, status='manual_review'):
        """Park the finding for a person without deleting the AI proposal."""
        self.ensure_one()
        vals = {
            'ai_needs_manual_review': True,
            'ai_review_reason': reason,
        }
        if status:
            vals['ai_status'] = status
        self.sudo().write(vals)

    # ------------------------------------------------------------------
    # Apply dispatch — one writer per fix type
    # ------------------------------------------------------------------

    def _ai_apply_to_target(self, target):
        """Write the stored proposal to ``target`` according to ai_fix_type."""
        self.ensure_one()
        fix_type = self.ai_fix_type or 'field'
        handler = {
            'field': self._ai_apply_field,
            'og_image': self._ai_apply_og_image,
            'schema': self._ai_apply_schema,
            'image_alt': self._ai_apply_image_alt,
            'thin_content': self._ai_apply_thin_content,
        }.get(fix_type)
        if not handler:
            raise UserError(_('Unknown AI fix type: %s', fix_type))
        handler(target)

    def _ai_payload(self):
        if not self.ai_fix_payload:
            return {}
        try:
            return json.loads(self.ai_fix_payload)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _ai_apply_field(self, target):
        """seo_title / seo_description (per language) or the slug."""
        if not self.ai_proposed_field or self.ai_proposed_value is None:
            raise UserError(_('No proposed value to apply.'))
        translations = {}
        if self.ai_proposed_translations:
            try:
                translations = json.loads(self.ai_proposed_translations)
            except (json.JSONDecodeError, TypeError):
                raise UserError(_(
                    'Stored AI translations are corrupt; '
                    're-run the suggestion.'))
        if translations:
            # Only write languages that are actually installed — the AI can
            # hallucinate lang codes, and with_context(lang=<bogus>) would
            # write into a non-existent translation context.
            installed = {code for code, _name
                         in self.env['res.lang'].get_installed()}
            for lang_code in list(translations):
                if lang_code not in installed:
                    _logger.warning(
                        'finding %d: skipping AI translation for '
                        'non-installed language %r', self.id, lang_code)
                    translations.pop(lang_code)
        if translations:
            for lang_code, value in translations.items():
                target.sudo().with_context(lang=lang_code).write(
                    {self.ai_proposed_field: value})
        else:
            target.sudo().write({self.ai_proposed_field: self.ai_proposed_value})

    def _ai_apply_og_image(self, target):
        """Set the OG image to the company logo."""
        if 'seo_og_image' not in target._fields:
            raise UserError(_('Target has no seo_og_image field.'))
        company = self.env.company
        if not company.logo:
            raise UserError(_('No company logo is configured to use as the OG image.'))
        target.sudo().write({'seo_og_image': company.logo})

    def _ai_apply_schema(self, target):
        """Attach a JSON-LD schema instance for the chosen template."""
        if 'era.seo.schema.instance' not in self.env:
            raise UserError(_('The schema engine is not installed.'))
        code = self._ai_payload().get('template_code')
        if not code:
            raise UserError(_('No schema template code in the proposal.'))
        Template = self.env['era.seo.schema.template'].sudo()
        tpl = Template.search([('code', '=', code), ('active', '=', True)], limit=1)
        if not tpl:
            raise UserError(_('Schema template "%s" no longer exists.', code))
        Instance = self.env['era.seo.schema.instance'].sudo()
        existing = Instance.search([
            ('template_id', '=', tpl.id),
            ('res_model', '=', target._name),
            ('res_id', '=', target.id),
        ], limit=1)
        if not existing:
            Instance.create({
                'template_id': tpl.id,
                'res_model': target._name,
                'res_id': target.id,
            })

    def _ai_apply_image_alt(self, target):
        """Inject the proposed alt text into the target's content images.

        Reads/writes in the language the images were scanned in (stamped on the
        payload as ``lang`` — the website default/served language). The same
        image can lack alt in one language version of the arch but not another,
        so applying in the admin's UI language could match nothing or patch the
        wrong version.
        """
        payload = self._ai_payload()
        pairs = payload.get('alts') or []
        if not pairs:
            raise UserError(_('No alt-text proposals to apply.'))
        lang = payload.get('lang')
        rec = target.with_context(lang=lang) if lang else target
        field = self._ai_content_field(rec)
        html_text = rec[field] or ''
        new_html, changed = self._inject_alt_text(
            html_text, pairs, self._ai_is_xml_field(field))
        if not changed:
            raise UserError(_('Could not match any image to inject alt text into.'))
        rec.sudo().write({field: new_html})

    # Odoo website "Text" snippet shell. The AI block is dropped INSIDE the
    # container so the inserted content is a proper, editable s_text_block
    # section (padding + columns) rather than loose markup. Built by string
    # concat (not str.format) so braces in the AI HTML can't break it.
    _THIN_CONTENT_SNIPPET_OPEN = (
        '<section class="s_text_block pt40 pb40 o_colored_level"'
        ' data-snippet="s_text_block" data-name="Text">'
        '<div class="container s_allow_columns">'
    )
    _THIN_CONTENT_SNIPPET_CLOSE = '</div></section>'

    def _ai_apply_thin_content(self, target):
        """Insert the proposed HTML block into the target's content, wrapped in
        a website "Text" (s_text_block) snippet and placed inside #wrap (above
        the footer).

        Reads and writes in the language the content was generated for (stamped
        on the payload as ``lang`` — the website default language). Essential on
        a multilang site: the real page content lives in that language version,
        so appending in another language context would append to an empty shell
        and lose the existing content.
        """
        payload = self._ai_payload()
        html = payload.get('html')
        if not html:
            raise UserError(_('No expansion HTML to apply.'))
        block = (self._THIN_CONTENT_SNIPPET_OPEN + html
                 + self._THIN_CONTENT_SNIPPET_CLOSE)
        lang = payload.get('lang')
        rec = target.with_context(lang=lang) if lang else target
        field = self._ai_content_field(rec)
        html_text = rec[field] or ''
        new_html = self._append_html(html_text, block, self._ai_is_xml_field(field))
        rec.sudo().write({field: new_html})

    # ------------------------------------------------------------------
    # Content read/write helpers (website.page arch is XML/QWeb; blog.post
    # content is HTML — keep both valid)
    # ------------------------------------------------------------------

    @staticmethod
    def _ai_content_field(target):
        """Name of the writable content field on the target ('content'/'arch')."""
        if 'content' in target._fields:
            return 'content'
        if 'arch' in target._fields:
            return 'arch'
        raise UserError(_('Target record has no editable content field.'))

    @staticmethod
    def _ai_is_xml_field(field):
        """website.page arch is QWeb XML — must stay valid XML on write."""
        return field == 'arch'

    @staticmethod
    def _inject_alt_text(html_text, pairs, is_xml):
        """Add alt attrs to <img> tags lacking them. Returns (new_html, changed).

        Matches by ``src`` first, then fills any still-empty imgs in document
        order from the remaining proposals. ``is_xml`` is driven by the target
        field, not the content: the QWeb ``arch`` is validated as XML on write,
        so it MUST round-trip through the XML parser (self-closing ``<img/>``);
        plain HTML fields use the HTML parser.
        """
        from lxml import etree, html as lxml_html
        if not html_text:
            return html_text, False
        try:
            if is_xml:
                root = etree.fromstring(html_text.encode('utf-8'))
            else:
                root = lxml_html.fragment_fromstring(html_text, create_parent='div')
            imgs = root.xpath('//img')
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('Could not parse the page content: %s', exc))

        by_src = {}
        for p in pairs:
            if p.get('src'):
                by_src.setdefault(p['src'], p['alt'])
        leftover = [p['alt'] for p in pairs]

        changed = False
        for img in imgs:
            if (img.get('alt') or '').strip():
                continue
            src = img.get('src') or img.get('data-src') or ''
            alt = by_src.get(src)
            if alt is None and leftover:
                alt = leftover.pop(0)
            if alt:
                img.set('alt', alt)
                changed = True
        if not changed:
            return html_text, False

        if is_xml:
            new_html = etree.tostring(root, encoding='unicode')
        else:
            new_html = ''.join(
                lxml_html.tostring(c, encoding='unicode') for c in root.iterchildren()
            )
        return new_html, True

    @staticmethod
    def _append_html(html_text, block, is_xml):
        """Append an HTML block to existing content, preserving XML validity.

        For plain HTML content we concatenate (the block lands at the bottom of
        the body, above the site footer which is part of the layout, not the
        content). For the QWeb ``arch`` (XML) the existing arch is parsed
        exactly (XML parser — no restructuring), while the small AI ``block``
        goes through the tolerant HTML parser so ``&nbsp;``, ``<br>`` and
        unclosed ``<img>`` don't break the append; serializing the whole tree
        as XML yields valid, self-closed markup.

        The block is inserted INTO the page's content container — ``#wrap``
        (falling back to the last ``oe_structure``) — NOT appended to the arch
        root. Appending to the root puts the block *after* ``<t t-call=
        "website.layout">``, which renders it below the entire layout (footer
        included); inserting into ``#wrap`` lands it at the end of the page
        body, ABOVE the footer, where editable page content belongs.
        """
        from lxml import etree, html as lxml_html
        if not is_xml:
            return (html_text or '') + '\n' + block
        try:
            root = etree.fromstring((html_text or '').encode('utf-8'))
            frag = lxml_html.fragment_fromstring(block, create_parent='div')
            # Prefer the #wrap content container; else the last oe_structure
            # editable zone; else fall back to the arch root (legacy behavior).
            container = None
            wraps = root.xpath('//*[@id="wrap"]')
            if wraps:
                container = wraps[-1]
            else:
                zones = root.xpath(
                    '//*[contains(concat(" ", normalize-space(@class), " "),'
                    ' " oe_structure ")]')
                container = zones[-1] if zones else root
            for child in list(frag):
                container.append(child)
            return etree.tostring(root, encoding='unicode')
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('Could not append content to the page: %s', exc))

    def _check_manager(self):
        # System-triggered runs (the weekly audit cron, the bulk-fill
        # cron, etc.) set `_era_ai_system=True` to bypass the per-user
        # gate. The admin opts into the unattended run by activating
        # those crons, so the cron user — usually the technical user,
        # not a SEO Manager — is allowed to act.
        if self.env.context.get('_era_ai_system'):
            return
        if not self.env.user.has_group('era_seo_suite.group_era_seo_manager'):
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
