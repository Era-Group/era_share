"""ERA SEO AI — proactive "fill SEO fields" action on the shared mixin.

Every SEO-bearing model (website.page, blog.post, era.blog.series /
category / author, and any future model carrying ``era.seo.mixin``) gets
``action_ai_fill_seo`` for free by extending the abstract mixin here.

The action asks the AI agent to produce ALL the recommended meta fields in
one call and writes back the empty ones (or every one, with ``overwrite``).
Because the JSON-LD schema templates read these same fields
(``record.seo_title``, ``record.seo_description``, ...), filling them also
enriches the rendered JSON-LD with no separate step.
"""
import json
import logging

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from .ai_client import AIClient, AIUnavailable

_logger = logging.getLogger(__name__)

# The core SEO meta the AI fills, in write order, with the per-field rule
# the prompt passes to the agent. All live on era.seo.mixin, so every host
# model gets them. Host models add their own via ``_ai_fill_fields`` —
# e.g. era_seo_blog_ai appends the blog subtitle/excerpt.
_CORE_FILL_SPECS = [
    {'name': 'seo_title',
     'rule': '<= 60 chars, primary keyword first, brand last if it fits, no ALL CAPS'},
    {'name': 'seo_description',
     'rule': '140-160 chars, one sentence ending with a period, soft CTA, '
             'keyword near the start'},
    {'name': 'seo_og_title',
     'rule': 'may equal seo_title, or a punchier social variant, <= 65 chars'},
    {'name': 'seo_og_description',
     'rule': 'may equal seo_description, or a social-friendly variant, <= 200 chars'},
    {'name': 'seo_keywords',
     'rule': '3-6 comma-separated terms actually supported by the content '
             '(the ONLY field allowed to be a comma list)'},
]

# Back-compat alias: the plain field-name tuple some callers may reference.
FILL_FIELDS = tuple(s['name'] for s in _CORE_FILL_SPECS)


class EraSeoMixin(models.AbstractModel):
    _inherit = 'era.seo.mixin'

    def _ai_fill_fields(self):
        """Return the AI-fillable field specs for this record.

        Each spec is ``{'name': <field>, 'rule': <prompt hint>}``. The field
        must be translatable so per-language fills land in the right
        translation. Host models override and ``super()`` + append their own
        SEO fields (the blog bridge adds ``era_subtitle`` / ``era_excerpt``).
        """
        return list(_CORE_FILL_SPECS)

    def action_ai_fill_seo(self):
        """Fill EMPTY recommended SEO fields on each record using the AI agent."""
        return self._ai_fill_seo(overwrite=False)

    def action_ai_rewrite_seo(self):
        """Regenerate ALL recommended SEO fields, overwriting existing values."""
        return self._ai_fill_seo(overwrite=True)

    def action_ai_fill_seo_and_schema(self):
        """Fill SEO meta AND ask the AI to pick the best JSON-LD schema.

        One click → meta + JSON-LD instance attached. If a schema instance is
        already attached, the pick step is skipped (we don't second-guess an
        explicit human choice).
        """
        self._ai_check_manager()
        # Step 1: fill empty SEO fields (existing behavior, including the
        # per-language notify). Side effect: writes back to the record.
        self._ai_fill_seo(overwrite=False)
        # Step 2: pick + attach a schema if none is set yet.
        attached = self._ai_pick_and_attach_schema()
        if attached:
            return self._ai_notify(
                'success',
                _('AI filled SEO fields and attached %(n)d schema instance(s).',
                  n=attached),
            )
        return self._ai_notify(
            'success',
            _('AI filled SEO fields. Schema instances were already attached — '
              'no change to schemas.'),
        )

    def _ai_pick_and_attach_schema(self):
        """For each record without a schema instance, ask the AI for the
        best-fit template and attach an instance. Returns count attached.

        Failures per-record are logged and skipped — they don't block the rest.
        """
        client = AIClient(self.env)
        ok, reason = client.is_available()
        if not ok:
            raise UserError(reason)

        Template = self.env['era.seo.schema.template'].sudo()
        Instance = self.env['era.seo.schema.instance'].sudo()
        templates = Template.search([('active', '=', True)])
        if not templates:
            return 0

        attached = 0
        for rec in self:
            existing = Instance.search([
                ('res_model', '=', rec._name),
                ('res_id', '=', rec.id),
            ], limit=1)
            if existing:
                continue
            try:
                pick = client.pick_schema(rec, templates)
            except (AIUnavailable, ValueError) as exc:
                _logger.warning(
                    'AI pick_schema skipped for %s#%s: %s',
                    rec._name, rec.id, exc)
                continue
            except Exception:  # noqa: BLE001
                _logger.exception(
                    'AI pick_schema failed for %s#%s', rec._name, rec.id)
                continue
            chosen = templates.filtered(lambda t: t.code == pick['code'])[:1]
            if not chosen:
                continue
            Instance.create({
                'res_model': rec._name,
                'res_id': rec.id,
                'template_id': chosen.id,
            })
            attached += 1
        return attached

    def action_add_schema_instance(self):
        """Open a dialog to attach a new JSON-LD schema instance.

        Used by the explicit "Add Schema" button — the embedded x2many list's
        "Add a line" link can be hard to spot on a fresh form, and a clearly
        labeled button surfaces the action.
        """
        self.ensure_one()
        return {
            'name': _('Add Schema'),
            'type': 'ir.actions.act_window',
            'res_model': 'era.seo.schema.instance',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
            },
        }

    def _ai_fill_seo(self, overwrite=False):
        self._ai_check_manager()
        client = AIClient(self.env)
        ok, reason = client.is_available()
        if not ok:
            raise UserError(reason)

        Log = self.env['era.seo.ai.fix.log'].sudo()
        filled_records = 0
        errors = []

        for rec in self:
            languages, _default = rec._ai_fill_languages()
            field_specs = rec._ai_fill_fields()
            field_names = [s['name'] for s in field_specs]
            written_fields = set()
            langs_done = []
            last_proposal = None
            raw_by_lang = {}

            for lang in languages:
                try:
                    proposal = client.fill_seo(
                        rec, overwrite=overwrite, lang=lang, field_specs=field_specs)
                except AIUnavailable as exc:
                    raise UserError(str(exc)) from exc
                except Exception as exc:  # noqa: BLE001
                    _logger.exception(
                        'AI fill_seo failed for %s#%s [%s]', rec._name, rec.id, lang.code)
                    errors.append(_('%s#%s [%s]: %s', rec._name, rec.id, lang.code, exc))
                    continue

                last_proposal = proposal
                raw_by_lang[lang.code] = proposal['raw_json']
                proposed = proposal['fields']
                lang_rec = rec.with_context(lang=lang.code)
                vals = {}
                for fname in field_names:
                    new_val = proposed.get(fname)
                    if not new_val:
                        continue
                    # "Fill missing" must look at THIS language's own stored
                    # translation — not lang_rec[fname], which falls back to the
                    # source-language value and would make every non-default
                    # language look already-filled (so Arabic kept showing the
                    # English text). Rewrite overwrites unconditionally.
                    if overwrite or rec._ai_lang_needs_fill(fname, lang.code):
                        vals[fname] = new_val
                if vals:
                    lang_rec.write(vals)
                    written_fields |= set(vals.keys())
                    langs_done.append(lang.code)

            if written_fields:
                filled_records += 1

            # Collect per-record error messages (from exceptions caught above).
            rec_errors = [e for e in errors if '%s#%s' % (rec._name, rec.id) in str(e)]
            Log.create({
                'check_code': 'ai_fill',
                'kind': 'fill',
                'target_model': rec._name,
                'target_id': rec.id,
                'target_url': getattr(rec, 'url', False) or rec._get_seo_path(),
                'model': last_proposal['model'] if last_proposal else False,
                'field_written': (
                    '{} ({})'.format(', '.join(sorted(written_fields)),
                                     ', '.join(langs_done))
                    if written_fields else '(nothing to fill)'
                ),
                'proposed_value': json.dumps(raw_by_lang, ensure_ascii=False, indent=2),
                'explanation': last_proposal.get('explanation', '') if last_proposal else '',
                'confidence': last_proposal.get('confidence', 0.0) if last_proposal else 0.0,
                'applied': bool(written_fields),
                'applied_date': fields.Datetime.now() if written_fields else False,
                'applied_user_id': self.env.user.id if written_fields else False,
                'error_message': '\n'.join(str(e) for e in rec_errors) if rec_errors else False,
            })

        if errors:
            return self._ai_notify(
                'warning',
                _('Filled %d record(s); %d error(s):\n%s',
                  filled_records, len(errors), '\n'.join(errors[:5])),
            )
        return self._ai_notify(
            'success',
            _('AI filled SEO fields on %d record(s) across all website languages.',
              filled_records),
        )

    def _ai_fill_languages(self):
        """Resolve the languages to generate for this record.

        Reuses era.seo.mixin._era_hreflang_languages (website-scoped) when
        present; falls back to all active res.lang.
        """
        self.ensure_one()
        try:
            langs, default_lang = self._era_hreflang_languages()
            if langs:
                return langs, default_lang
        except Exception:  # noqa: BLE001
            pass
        active = self.env['res.lang'].search([('active', '=', True)])
        return active, active[:1]

    def _ai_lang_needs_fill(self, fname, lang_code):
        """True when ``fname`` has no value of its OWN in ``lang_code``.

        For translatable fields, ``rec.with_context(lang=X)[fname]`` returns
        the source-language value as a fallback when X has no translation, so
        it can't tell "translated" from "falling back". We read the raw stored
        translations instead, so "fill missing" actually fills each language
        that lacks its own translation (e.g. Arabic showing the English text).
        """
        self.ensure_one()
        field = self._fields.get(fname)
        if field is None:
            return False
        if not getattr(field, 'translate', False):
            return not self[fname]
        try:
            stored = field._get_stored_translations(self)
        except Exception:  # noqa: BLE001
            # API moved — fall back to the (imperfect) context read.
            return not self.with_context(lang=lang_code)[fname]
        if not stored:
            return True
        value = stored.get(lang_code)
        return not (value and str(value).strip())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ai_check_manager(self):
        # System-triggered automations (e.g. era_seo_blog_ai auto-rebuild on
        # content change) run on behalf of whoever edited the content, who is
        # not necessarily a SEO manager. The admin opts into the automation by
        # enabling AI auto-fix, so the per-user gate doesn't apply there.
        if self.env.context.get('_era_ai_system'):
            return
        if not self.env.user.has_group('era_seo_suite.group_era_seo_manager'):
            raise AccessError(_('AI SEO fill requires the SEO Manager group.'))

    @staticmethod
    def _ai_notify(kind, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': kind,
                'message': message,
                'sticky': kind == 'warning',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
