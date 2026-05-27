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
import logging

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from .ai_client import AIClient, AIUnavailable

_logger = logging.getLogger(__name__)

# Fields the AI fills, in write order. All live on era.seo.mixin so every
# host model has them.
FILL_FIELDS = (
    'seo_title',
    'seo_description',
    'seo_og_title',
    'seo_og_description',
    'seo_keywords',
)


class EraSeoMixin(models.AbstractModel):
    _inherit = 'era.seo.mixin'

    def action_ai_fill_seo(self):
        """Fill EMPTY recommended SEO fields on each record using the AI agent."""
        return self._ai_fill_seo(overwrite=False)

    def action_ai_rewrite_seo(self):
        """Regenerate ALL recommended SEO fields, overwriting existing values."""
        return self._ai_fill_seo(overwrite=True)

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
            try:
                proposal = client.fill_seo(rec, overwrite=overwrite)
            except AIUnavailable as exc:
                raise UserError(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                _logger.exception('AI fill_seo failed for %s#%s', rec._name, rec.id)
                Log.create({
                    'check_code': 'ai_fill',
                    'kind': 'fill',
                    'target_model': rec._name,
                    'target_id': rec.id,
                    'target_url': getattr(rec, 'url', False) or rec._get_seo_path(),
                    'error_message': str(exc),
                })
                errors.append(_('%s#%s: %s', rec._name, rec.id, exc))
                continue

            proposed = proposal['fields']
            vals = {}
            for fname in FILL_FIELDS:
                new_val = proposed.get(fname)
                if not new_val:
                    continue
                if overwrite or not rec[fname]:
                    vals[fname] = new_val
            if vals:
                rec.write(vals)
                filled_records += 1

            Log.create({
                'check_code': 'ai_fill',
                'kind': 'fill',
                'target_model': rec._name,
                'target_id': rec.id,
                'target_url': getattr(rec, 'url', False) or rec._get_seo_path(),
                'model': proposal['model'],
                'field_written': ', '.join(vals.keys()) or '(no empty fields)',
                'proposed_value': proposal['raw_json'],
                'explanation': proposal.get('explanation', ''),
                'confidence': proposal.get('confidence', 0.0),
                'applied': bool(vals),
                'applied_date': fields.Datetime.now() if vals else False,
                'applied_user_id': self.env.user.id if vals else False,
            })

        if errors:
            return self._ai_notify(
                'warning',
                _('Filled %d record(s); %d error(s):\n%s',
                  filled_records, len(errors), '\n'.join(errors[:5])),
            )
        return self._ai_notify(
            'success', _('AI filled SEO fields on %d record(s).', filled_records),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ai_check_manager(self):
        if not self.env.user.has_group('era_seo_manager.group_era_seo_manager'):
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
