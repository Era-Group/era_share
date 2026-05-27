"""Auto-rebuild a blog post's SEO meta when its content changes.

Per the product decision: every edit to ``blog.post.content`` regenerates
ALL the SEO meta fields (title, description, OG, keywords) from the new
content, across every installed website language — so the meta and the
JSON-LD that reads those fields never drift from the body.

Design notes:
- Runs only when **AI Auto-Fix is enabled** (``era_seo.ai_enabled``).
- Runs as a *system* automation: the editor who changed the content does
  not need the SEO-Manager group (the admin opted in by enabling AI).
- **Best-effort**: a failed/unavailable/slow AI call is caught and logged,
  never blocking the content save (the ``super().write`` already ran).
- No recursion: the regenerated values are SEO fields, not ``content``, and
  the write carries ``_era_ai_no_rebuild`` so it can't re-trigger.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)

_ENABLED_TRUE = ('True', '1', 'true', 'yes', 'on')


class BlogPost(models.Model):
    _inherit = 'blog.post'

    def write(self, vals):
        result = super().write(vals)
        if self._era_ai_should_rebuild(vals):
            for post in self:
                post._era_ai_rebuild_seo()
        return result

    # ------------------------------------------------------------------

    def _era_ai_should_rebuild(self, vals):
        """True when a content edit should trigger an AI SEO rebuild."""
        if 'content' not in vals:
            return False
        if self.env.context.get('_era_ai_no_rebuild'):
            return False
        return self._era_ai_enabled()

    def _era_ai_enabled(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return ICP.get_param('era_seo.ai_enabled', 'False') in _ENABLED_TRUE

    def _era_ai_rebuild_seo(self):
        """Regenerate ALL SEO meta from the current content. Best-effort."""
        self.ensure_one()
        try:
            self.with_context(
                _era_ai_no_rebuild=True,   # the SEO writes must not recurse
                _era_ai_system=True,       # automation: skip the per-user gate
            )._ai_fill_seo(overwrite=True)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'blog.post %s: AI SEO auto-rebuild skipped (%s)', self.id, exc)
