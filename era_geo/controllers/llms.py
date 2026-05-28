"""ERA GEO — /llms.txt controller.

Serves a Markdown site map for AI answer engines following the llmstxt.org
convention:

    # Site Name

    > One-line summary

    ## Pages
    - [Title](https://site/url): description
    ...

``/llms-full.txt`` is the same index with longer per-item descriptions.
Both are public, cached for an hour, and built from published content +
the ERA SEO titles/descriptions.
"""
import logging
import re

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_TRUE = ('True', '1', 'true', 'yes', 'on')


def _icp(key, default=None):
    return request.env['ir.config_parameter'].sudo().get_param(key, default)


def _enabled():
    return _icp('era_geo.llms_enabled', 'True') in _TRUE


def _clean(text, limit):
    """Collapse whitespace / strip HTML to a single-line description."""
    if not text:
        return ''
    text = re.sub(r'<[^>]+>', ' ', text)
    text = ' '.join(text.split())
    return text[:limit].rstrip()


class EraGeoLlms(http.Controller):

    @http.route('/llms.txt', type='http', auth='public', website=True,
                sitemap=False)
    def llms_txt(self, **kw):
        if not _enabled():
            return request.not_found()
        return self._respond(self._build(full=False))

    @http.route('/llms-full.txt', type='http', auth='public', website=True,
                sitemap=False)
    def llms_full_txt(self, **kw):
        if not _enabled():
            return request.not_found()
        return self._respond(self._build(full=True))

    # ------------------------------------------------------------------

    @staticmethod
    def _respond(body):
        return request.make_response(body, headers=[
            ('Content-Type', 'text/plain; charset=utf-8'),
            ('Cache-Control', 'public, max-age=3600'),
        ])

    def _build(self, full=False):
        website = request.website
        base = request.httprequest.url_root.rstrip('/')
        desc_limit = 300 if full else 160
        max_items = int(_icp('era_geo.llms_max_items', '100') or 100)

        name = website.name or request.env.company.name or 'Website'
        summary = _icp('era_geo.site_summary') or request.env.company.name or ''

        lines = ['# %s' % name, '']
        if summary:
            lines += ['> %s' % _clean(summary, 200), '']

        page_lines = self._page_lines(base, desc_limit, max_items)
        if page_lines:
            lines += ['## Pages', ''] + page_lines + ['']

        if _icp('era_geo.llms_include_blog', 'True') in _TRUE:
            blog_lines = self._blog_lines(base, desc_limit, max_items)
            if blog_lines:
                lines += ['## Blog', ''] + blog_lines + ['']

        return '\n'.join(lines).rstrip() + '\n'

    def _page_lines(self, base, desc_limit, max_items):
        website = request.website
        Page = request.env['website.page'].sudo()
        domain = [
            ('website_published', '=', True),
            ('url', '=like', '/%'),
            '|', ('website_id', '=', website.id), ('website_id', '=', False),
        ]
        out = []
        for page in Page.search(domain, limit=max_items):
            url = page.url or ''
            # Skip dynamic / templated / system routes.
            if not url.startswith('/') or '<' in url or url.startswith('/website'):
                continue
            title = (getattr(page, 'seo_title', False) or page.name or url).strip()
            desc = _clean(getattr(page, 'seo_description', '') or '', desc_limit)
            line = '- [%s](%s%s)' % (title, base, url)
            if desc:
                line += ': %s' % desc
            out.append(line)
        return out

    def _blog_lines(self, base, desc_limit, max_items):
        if 'blog.post' not in request.env:
            return []
        Post = request.env['blog.post'].sudo()
        try:
            posts = Post.search([('website_published', '=', True)], limit=max_items)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for post in posts:
            url = getattr(post, 'website_url', None) or ''
            if not url:
                continue
            title = (getattr(post, 'seo_title', False) or post.name or '').strip()
            desc = _clean(
                getattr(post, 'seo_description', '')
                or getattr(post, 'era_excerpt', '') or '', desc_limit)
            line = '- [%s](%s%s)' % (title, base, url)
            if desc:
                line += ': %s' % desc
            out.append(line)
        return out
