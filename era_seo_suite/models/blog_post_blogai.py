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
import re

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

_ENABLED_TRUE = ('True', '1', 'true', 'yes', 'on')

# Don't spend an AI call on a near-empty post: skip the auto-rebuild when the
# new content has fewer than this many words of visible text.
_MIN_CONTENT_WORDS = 50


class BlogPost(models.Model):
    _inherit = 'blog.post'

    era_ai_generated_at = fields.Datetime(
        string='AI Generated At', readonly=True, index=True,
        help='Set automatically when the auto-publish cron creates this post.')
    era_ai_trend_signal = fields.Char(
        string='Trend Signal', readonly=True,
        help='The trend (often from Google Trends) the AI cited as the '
             'reason for writing this article.')
    era_ai_confidence = fields.Float(
        string='AI Confidence', readonly=True, digits=(3, 2),
        help='Self-reported confidence (0.0-1.0) when the agent proposed '
             'this article.')

    def action_era_ai_regenerate(self):
        """Replace this post's title / subtitle / content / SEO meta with a
        fresh AI proposal — keeps the same record, useful when the original
        run produced something thin or off-tone. Trend signal and confidence
        are also refreshed.
        """
        self.ensure_one()
        Hub = self.env['era.seo.suite.hub'].sudo()
        from .ai_client import AIClient, AIUnavailable
        client = AIClient(self.env)
        ok, reason = client.is_available()
        if not ok:
            from odoo.exceptions import UserError
            raise UserError(reason)
        ICP = self.env['ir.config_parameter'].sudo()
        Category = self.env['era.blog.category'].sudo()
        business_context = {
            'org_name': ICP.get_param('era_seo.organization_name', ''),
            'summary':  ICP.get_param('era_seo_suite.site_summary', ''),
        }
        past_titles = self.env['blog.post'].sudo().search(
            [('id', '!=', self.id)], order='id desc', limit=30).mapped('name')
        existing_categories = Category.search([], limit=50).mapped('name')
        article = client.propose_article(
            business_context, past_titles, existing_categories,
            lang_code=(ICP.get_param('era_seo.article_lang', '') or '').strip() or None,
            trending_now=Hub._fetch_google_trends(),
            prompt_addendum=(ICP.get_param('era_seo.article_prompt_addendum', '') or '').strip() or None,
        )
        vals = {
            'name': article['title'],
            'content': article['content_html'],
            'era_ai_generated_at': fields.Datetime.now(),
            'era_ai_trend_signal': article.get('trend_signal') or '',
            'era_ai_confidence': float(article.get('confidence') or 0.0),
        }
        for fname, src in [
            ('seo_title', 'seo_title'),
            ('seo_description', 'seo_description'),
            ('seo_keywords', 'seo_keywords'),
            ('era_subtitle', 'subtitle'),
        ]:
            if fname in self._fields and article.get(src):
                vals[fname] = article[src]
        self.write(vals)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('Regenerated: %s', article['title']),
                'sticky': False,
            },
        }

    def action_era_open_website(self):
        """Open this post's live URL in a new tab."""
        self.ensure_one()
        url = getattr(self, 'website_url', '') or ''
        if not url:
            from odoo.exceptions import UserError
            raise UserError(_('This post has no website URL yet.'))
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def _ai_fill_fields(self):
        """Add the blog-specific SEO/content fields to the AI fill set.

        Both are translatable, so the fill writes a value per installed
        website language. ``era_excerpt`` also feeds the meta description
        fallback and the BlogPosting JSON-LD, so filling it improves both.
        """
        specs = super()._ai_fill_fields()
        specs += [
            {'name': 'era_subtitle',
             'rule': 'a short editorial subtitle/dek that complements the title, '
                     '<= 120 chars, no trailing period'},
            {'name': 'era_excerpt',
             'rule': '1-2 sentence plain-text summary for feeds and blog list '
                     'cards, <= 300 chars, no HTML'},
        ]
        return specs

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
        if not self._era_ai_enabled():
            return False
        # Skip thin posts — too little signal to generate good SEO, and not
        # worth an AI call. Measured in words on the new content's visible text.
        if self._era_ai_word_count(vals.get('content')) < _MIN_CONTENT_WORDS:
            return False
        return True

    def _era_ai_enabled(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return ICP.get_param('era_seo.ai_enabled', 'False') in _ENABLED_TRUE

    @staticmethod
    def _era_ai_word_count(html):
        """Number of words of visible text in an HTML fragment (tags stripped)."""
        if not html:
            return 0
        try:
            from lxml import html as lxml_html
            text = (lxml_html.fragment_fromstring(
                html, create_parent='div').text_content() or '')
        except Exception:  # noqa: BLE001
            text = re.sub(r'<[^>]+>', ' ', html)
        return len(text.split())

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
