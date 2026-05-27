"""ERA SEO Blog — feed controllers (RSS 2.0, Atom 1.0, JSON Feed 1.1).

Routes (all `auth='public'`, `website=True`, cached 10 min via Cache-Control,
``sitemap=False`` so feed URLs don't pollute the sitemap):

  /blog/feed/rss                       — every blog
  /blog/feed/atom
  /blog/feed/json
  /blog/tag/<tag_slug>/feed/<format>
  /blog/category/<slug>/feed/<format>
  /blog/author/<slug>/feed/<format>
  /blog/series/<slug>/feed/<format>

The route trio rss/atom/json share a single domain-resolution helper so all
filter variants stay consistent (same item set, three serializations).

Per `era_seo_manager` SPEC §11.9 (extracted to this addon).
"""
import json
import logging
from datetime import timezone
from email.utils import format_datetime

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_FEED_LIMIT = 50
_FEED_CACHE_SECONDS = 600


class EraBlogFeedController(http.Controller):

    @http.route('/blog/feed/rss', type='http', auth='public', website=True, sitemap=False)
    def blog_feed_rss(self, **kw):
        return self._render('rss', [])

    @http.route('/blog/feed/atom', type='http', auth='public', website=True, sitemap=False)
    def blog_feed_atom(self, **kw):
        return self._render('atom', [])

    @http.route('/blog/feed/json', type='http', auth='public', website=True, sitemap=False)
    def blog_feed_json(self, **kw):
        return self._render('json', [])

    @http.route('/blog/tag/<string:tag_slug>/feed/<string:format>',
                type='http', auth='public', website=True, sitemap=False)
    def blog_feed_by_tag(self, tag_slug, format, **kw):
        if format not in ('rss', 'atom', 'json'):
            return request.not_found()
        tag = self._resolve_tag(tag_slug)
        if not tag:
            return request.not_found()
        return self._render(format, [('tag_ids', 'in', tag.ids)], title_suffix=tag.name)

    @http.route('/blog/category/<string:slug>/feed/<string:format>',
                type='http', auth='public', website=True, sitemap=False)
    def blog_feed_by_category(self, slug, format, **kw):
        if format not in ('rss', 'atom', 'json'):
            return request.not_found()
        cat = request.env['era.blog.category'].sudo().search(
            [('slug', '=', slug), ('active', '=', True)], limit=1,
        )
        if not cat:
            return request.not_found()
        return self._render(format, [('era_category_id', '=', cat.id)],
                            title_suffix=cat.name)

    @http.route('/blog/author/<string:slug>/feed/<string:format>',
                type='http', auth='public', website=True, sitemap=False)
    def blog_feed_by_author(self, slug, format, **kw):
        if format not in ('rss', 'atom', 'json'):
            return request.not_found()
        author = request.env['era.blog.author'].sudo().search(
            [('slug', '=', slug), ('active', '=', True)], limit=1,
        )
        if not author:
            return request.not_found()
        return self._render(format, [('era_author_profile_id', '=', author.id)],
                            title_suffix=author.name)

    @http.route('/blog/series/<string:slug>/feed/<string:format>',
                type='http', auth='public', website=True, sitemap=False)
    def blog_feed_by_series(self, slug, format, **kw):
        if format not in ('rss', 'atom', 'json'):
            return request.not_found()
        series = request.env['era.blog.series'].sudo().search(
            [('slug', '=', slug), ('active', '=', True)], limit=1,
        )
        if not series:
            return request.not_found()
        return self._render(format, [('era_series_id', '=', series.id)],
                            title_suffix=series.name)

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    def _resolve_tag(self, slug):
        from ..utils import slugify
        tags = request.env['blog.tag'].sudo().search([])
        for tag in tags:
            if slugify(tag.name) == slug:
                return tag
        return None

    def _render(self, fmt, extra_domain, title_suffix=None):
        domain = [('is_published', '=', True)] + (extra_domain or [])
        try:
            website = request.env['website'].get_current_website()
            if website:
                domain += ['|',
                           ('blog_id.website_id', '=', website.id),
                           ('blog_id.website_id', '=', False)]
        except Exception:  # noqa: BLE001
            pass

        posts = request.env['blog.post'].sudo().search(
            domain, limit=_FEED_LIMIT, order='published_date desc, id desc',
        )

        base_url = request.env['ir.config_parameter'].sudo().get_param(
            'web.base.url', ''
        ).rstrip('/')
        website_name = 'Blog'
        try:
            website = request.env['website'].get_current_website()
            if website:
                website_name = website.name or website_name
        except Exception:  # noqa: BLE001
            pass
        title = website_name + (f' — {title_suffix}' if title_suffix else '') + ' — Blog'

        if fmt == 'rss':
            body, ctype = self._render_rss(posts, base_url, title)
        elif fmt == 'atom':
            body, ctype = self._render_atom(posts, base_url, title)
        else:
            body, ctype = self._render_json(posts, base_url, title)

        return request.make_response(
            body,
            headers=[
                ('Content-Type', ctype),
                ('Cache-Control', f'public, max-age={_FEED_CACHE_SECONDS}'),
            ],
        )

    # ------------------------------------------------------------------------
    # Format-specific serializers
    # ------------------------------------------------------------------------

    @staticmethod
    def _escape_xml(text):
        if not text:
            return ''
        return (
            str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;')
        )

    @staticmethod
    def _rfc822(dt):
        if not dt:
            return ''
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return format_datetime(dt)

    @staticmethod
    def _iso8601(dt):
        if not dt:
            return ''
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    def _render_rss(self, posts, base_url, title):
        items = []
        for p in posts:
            link = f'{base_url}{p.website_url}' if p.website_url else base_url
            desc = p.era_excerpt_effective or ''
            pub = self._rfc822(p.published_date)
            items.append(
                '<item>'
                f'<title>{self._escape_xml(p.name)}</title>'
                f'<link>{self._escape_xml(link)}</link>'
                f'<guid isPermaLink="true">{self._escape_xml(link)}</guid>'
                f'<description>{self._escape_xml(desc)}</description>'
                f'<pubDate>{pub}</pubDate>'
                '</item>'
            )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0">'
            '<channel>'
            f'<title>{self._escape_xml(title)}</title>'
            f'<link>{self._escape_xml(base_url)}</link>'
            f'<description>{self._escape_xml(title)}</description>'
            + ''.join(items) +
            '</channel></rss>'
        )
        return body, 'application/rss+xml; charset=utf-8'

    def _render_atom(self, posts, base_url, title):
        items = []
        for p in posts:
            link = f'{base_url}{p.website_url}' if p.website_url else base_url
            desc = p.era_excerpt_effective or ''
            updated = self._iso8601(p.write_date or p.published_date)
            published = self._iso8601(p.published_date)
            items.append(
                '<entry>'
                f'<id>{self._escape_xml(link)}</id>'
                f'<title>{self._escape_xml(p.name)}</title>'
                f'<link rel="alternate" href="{self._escape_xml(link)}"/>'
                f'<summary>{self._escape_xml(desc)}</summary>'
                f'<updated>{updated}</updated>'
                + (f'<published>{published}</published>' if published else '') +
                '</entry>'
            )
        feed_id = base_url + '/blog'
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            f'<id>{self._escape_xml(feed_id)}</id>'
            f'<title>{self._escape_xml(title)}</title>'
            f'<link rel="self" href="{self._escape_xml(base_url + "/blog/feed/atom")}"/>'
            f'<updated>{self._iso8601(posts and posts[0].write_date or None)}</updated>'
            + ''.join(items) +
            '</feed>'
        )
        return body, 'application/atom+xml; charset=utf-8'

    def _render_json(self, posts, base_url, title):
        items = []
        for p in posts:
            link = f'{base_url}{p.website_url}' if p.website_url else base_url
            items.append({
                'id': link,
                'url': link,
                'title': p.name,
                'summary': p.era_excerpt_effective or '',
                'date_published': self._iso8601(p.published_date),
                'date_modified': self._iso8601(p.write_date),
                'tags': p.tag_ids.mapped('name'),
            })
        body = json.dumps({
            'version': 'https://jsonfeed.org/version/1.1',
            'title': title,
            'home_page_url': base_url + '/blog',
            'feed_url': base_url + '/blog/feed/json',
            'items': items,
        }, ensure_ascii=False, default=str)
        return body, 'application/feed+json; charset=utf-8'
