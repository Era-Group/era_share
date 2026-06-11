"""ERA SEO AI — AI-powered GEO content review for website pages.

The rule-based GEO checks (geo_no_faq, geo_low_factual_density, …) catch
mechanical defects. This adds a per-page *editorial* review: the LLM judges how
well a page can be understood, extracted and cited by AI answer engines and
records its verdict as audit findings under stable ``geo_ai_*`` codes — so the
recommendations live alongside the rule-based ones in Audit Findings, but with
human-quality, page-specific advice (dialect vs MSA, vague claims, weak
headings, missing tables / case studies / English, entity clarity).

On-demand and per-page (cost-aware): triggered from the page form button, run
on the selected page(s). Re-running refreshes the findings — dimensions the AI
no longer flags are auto-resolved.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .ai_client import AIClient, AIUnavailable

_logger = logging.getLogger(__name__)

_GEO_AI_DOMAIN_PREFIX = 'geo_ai_'


class WebsitePage(models.Model):
    _inherit = 'website.page'

    def action_ai_geo_review(self):
        """Run the AI GEO content review on the selected page(s).

        Records the LLM's verdict as ``geo_ai_*`` audit findings and opens the
        resulting recommendations. Gated on the SEO Manager group; commits per
        page so a slow batch that outlasts the request timeout keeps the work
        already done.
        """
        self._era_geo_check_manager()
        client = AIClient(self.env)
        ok, reason = client.is_available()
        if not ok:
            raise UserError(reason)

        errors = []
        for page in self:
            try:
                issues = client.geo_content_review(page)
            except AIUnavailable as exc:
                raise UserError(str(exc))
            except Exception as exc:  # noqa: BLE001
                _logger.exception('AI GEO review failed for page %s', page.id)
                errors.append(_('%s: %s', page.url or page.id, exc))
                continue
            page._ai_geo_apply_issues(issues)
            self.env.cr.commit()

        if errors:
            return self._era_geo_notify(
                'warning',
                _('GEO review finished with %d error(s):\n%s',
                  len(errors), '\n'.join(errors[:5])))
        return self._era_open_geo_findings()

    def _ai_geo_apply_issues(self, issues):
        """Upsert ``geo_ai_*`` findings for this page from ``issues`` and
        resolve any prior ``geo_ai_*`` finding whose dimension is no longer
        flagged. Returns the number of active issues. Never calls the LLM —
        directly unit-testable."""
        self.ensure_one()
        Finding = self.env['era.seo.audit.finding'].sudo()
        seen = set()
        for item in issues:
            code = item.get('code')
            if not code:
                continue
            seen.add(code)
            vals = {
                'severity': item.get('severity') or 'info',
                'check_name': item.get('title') or code,
                'details': item.get('detail') or '',
                'suggested_fix': item.get('recommendation') or '',
                'url': self.url or '/',
                'is_resolved': False,
                'resolved_date': False,
                'resolved_user_id': False,
            }
            existing = Finding.search([
                ('check_code', '=', code),
                ('res_model', '=', self._name),
                ('res_id', '=', self.id),
                ('lang_id', '=', False),
            ], limit=1)
            if existing:
                existing.write(vals)
            else:
                vals.update({
                    'check_code': code,
                    'res_model': self._name,
                    'res_id': self.id,
                })
                Finding.create(vals)
        # Resolve dimensions that are no longer flagged (the page improved).
        stale = Finding.search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('check_code', '=like', _GEO_AI_DOMAIN_PREFIX + '%'),
            ('is_resolved', '=', False),
        ]).filtered(lambda f: f.check_code not in seen)
        if stale:
            stale.write({
                'is_resolved': True,
                'resolved_date': fields.Datetime.now(),
                'resolved_user_id': self.env.user.id,
                'resolved_manually': False,
            })
        return len(seen)

    def _era_open_geo_findings(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('GEO Content Recommendations'),
            'res_model': 'era.seo.audit.finding',
            'view_mode': 'list,form',
            'domain': [
                ('res_model', '=', self._name),
                ('res_id', 'in', self.ids),
                ('check_code', '=like', _GEO_AI_DOMAIN_PREFIX + '%'),
                ('is_resolved', '=', False),
            ],
            'target': 'current',
        }

    def _era_geo_check_manager(self):
        if self.env.context.get('_era_ai_system'):
            return
        if not self.env.user.has_group('era_seo_suite.group_era_seo_manager'):
            raise AccessError(_('The AI GEO review requires the SEO Manager group.'))

    @staticmethod
    def _era_geo_notify(kind, message):
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
