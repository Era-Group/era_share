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

from odoo import _, api, fields, models
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
    # Richer fixes (19.0.7.0.0):
    'missing_og_image',     # mechanical: company logo
    'missing_schema',       # AI picks a JSON-LD template, attaches an instance
    'image_missing_alt',    # AI writes alt text, injected into the content imgs
    'thin_content',         # AI proposes an HTML block, appended on apply
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
            target = rec._ai_resolve_target()
            if not target:
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
                    })
                    if rec.ai_last_log_id:
                        rec.ai_last_log_id.sudo().write({
                            'applied': True,
                            'applied_date': fields.Datetime.now(),
                            'applied_user_id': self.env.user.id,
                        })
            except Exception as exc:  # noqa: BLE001
                errors.append(_('Finding %s: %s', rec.id, exc))
                continue
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
            translations = json.loads(self.ai_proposed_translations)
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
        """Inject the proposed alt text into the target's content images."""
        pairs = self._ai_payload().get('alts') or []
        if not pairs:
            raise UserError(_('No alt-text proposals to apply.'))
        field = self._ai_content_field(target)
        html_text = target[field] or ''
        new_html, changed = self._inject_alt_text(
            html_text, pairs, self._ai_is_xml_field(field))
        if not changed:
            raise UserError(_('Could not match any image to inject alt text into.'))
        target.sudo().write({field: new_html})

    def _ai_apply_thin_content(self, target):
        """Append the proposed HTML block to the target's content."""
        html = self._ai_payload().get('html')
        if not html:
            raise UserError(_('No expansion HTML to apply.'))
        field = self._ai_content_field(target)
        html_text = target[field] or ''
        new_html = self._append_html(html_text, html, self._ai_is_xml_field(field))
        target.sudo().write({field: new_html})

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

        For plain HTML content we concatenate. For the QWeb ``arch`` (XML)
        the existing arch is parsed exactly (XML parser — no restructuring),
        while the small AI ``block`` goes through the tolerant HTML parser so
        ``&nbsp;``, ``<br>`` and unclosed ``<img>`` don't break the append;
        serializing the whole tree as XML yields valid, self-closed markup.
        """
        from lxml import etree, html as lxml_html
        if not is_xml:
            return (html_text or '') + '\n' + block
        try:
            root = etree.fromstring((html_text or '').encode('utf-8'))
            frag = lxml_html.fragment_fromstring(block, create_parent='div')
            for child in list(frag):
                root.append(child)
            return etree.tostring(root, encoding='unicode')
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('Could not append content to the page: %s', exc))

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
