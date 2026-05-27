"""ERA SEO Blog — blog.post extension.

Adds 17 fields and several computes to stock ``blog.post``:

  - era_subtitle: optional secondary headline
  - era_reading_time_minutes, era_word_count: derived from content
  - era_excerpt: auto-generated if empty
  - era_series_id / era_series_sequence: position in a series
  - era_category_id: taxonomic category (distinct from tags)
  - era_related_post_ids: top 4 by series + tag overlap, computed stored
  - era_toc_html: nested <ul> built from h2/h3 in content
  - era_show_toc / era_show_author_box / era_show_related / era_show_share_buttons
  - era_faq_ids: inline FAQ for FAQPage schema
  - era_author_profile_id: standalone author profile (decoupled from res.users)
  - era_canonical_external_url: for syndicated content

Reading time uses 200 wpm (standard). Related posts: up to 2 from same
series, the rest by descending tag overlap, then by published_date.

Inherits ``era.seo.mixin`` here too — era_seo_manager added the mixin via a
deleted stub in earlier phases; the inheritance is reapplied in this addon
so the post form's SEO tab keeps working when only this addon is installed.

Per `era_seo_manager` SPEC §11.1-11.4 (extracted to this addon).
"""
import logging
import re

from lxml import html as lxml_html
from markupsafe import Markup

from odoo import api, fields, models
from ..utils import slugify

_logger = logging.getLogger(__name__)

_WORDS_PER_MINUTE = 200
_TAG_SPLIT_RE = re.compile(r'\s+')

# Schema template codes auto-attached to every blog post.
_AUTO_SCHEMA_CODES = ('blog_posting', 'breadcrumb_list')
# Code of the FAQ-only template; attached lazily when the post gains FAQs.
_FAQ_SCHEMA_CODE = 'blog_faq_page'


class BlogPost(models.Model):
    """blog.post + era.seo.mixin + Phase 5 enhancements."""

    _name = 'blog.post'
    _inherit = ['blog.post', 'era.seo.mixin']

    # --- Optional secondary headline -----------------------------------------

    era_subtitle = fields.Char(string='Subtitle', translate=True)

    # --- Reading time / word count -------------------------------------------

    era_word_count = fields.Integer(
        string='Word Count',
        compute='_compute_era_text_stats',
        store=True,
        readonly=True,
    )
    era_reading_time_minutes = fields.Integer(
        string='Reading Time (min)',
        compute='_compute_era_text_stats',
        store=True,
        readonly=True,
    )

    # --- Excerpt -------------------------------------------------------------

    era_excerpt = fields.Text(
        string='Excerpt',
        translate=True,
        help='Short summary shown in lists and feeds. If left empty, the '
             'first ~200 chars of the post content are used.',
    )
    era_excerpt_effective = fields.Text(
        string='Excerpt (effective)',
        compute='_compute_era_excerpt_effective',
        store=False,
    )

    # --- Series --------------------------------------------------------------

    era_series_id = fields.Many2one(
        'era.blog.series',
        string='Series',
        ondelete='set null',
        index=True,
    )
    era_series_sequence = fields.Integer(
        string='Position in Series',
        default=10,
    )
    era_series_prev_id = fields.Many2one(
        'blog.post',
        string='Previous in Series',
        compute='_compute_era_series_neighbors',
        store=False,
    )
    era_series_next_id = fields.Many2one(
        'blog.post',
        string='Next in Series',
        compute='_compute_era_series_neighbors',
        store=False,
    )

    # --- Category ------------------------------------------------------------

    era_category_id = fields.Many2one(
        'era.blog.category',
        string='Category',
        ondelete='set null',
        index=True,
    )

    # --- Related posts -------------------------------------------------------

    era_related_post_ids = fields.Many2many(
        'blog.post',
        'era_blog_post_related_rel',
        'post_id',
        'related_id',
        string='Related Posts',
        compute='_compute_era_related_posts',
        store=True,
    )

    # --- Auto-TOC ------------------------------------------------------------

    era_toc_html = fields.Html(
        string='Table of Contents',
        compute='_compute_era_toc_html',
        sanitize=False,
        store=True,
    )

    # --- Display toggles -----------------------------------------------------

    era_show_toc = fields.Boolean(default=True, string='Show TOC')
    era_show_author_box = fields.Boolean(default=True, string='Show Author Box')
    era_show_related = fields.Boolean(default=True, string='Show Related Posts')
    era_show_share_buttons = fields.Boolean(default=True, string='Show Share Buttons')

    # --- FAQ -----------------------------------------------------------------

    era_faq_ids = fields.One2many(
        'era.blog.faq',
        'post_id',
        string='FAQ Entries',
    )

    # --- Author profile ------------------------------------------------------

    era_author_profile_id = fields.Many2one(
        'era.blog.author',
        string='Author Profile',
        ondelete='set null',
        index=True,
    )

    # --- Syndication ---------------------------------------------------------

    era_canonical_external_url = fields.Char(
        string='Canonical (External)',
        help='When the post was originally published elsewhere, set the '
             'canonical URL here so search engines credit the source.',
    )

    # ------------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------------

    @api.depends('content')
    def _compute_era_text_stats(self):
        for rec in self:
            text = self._strip_html_to_text(rec.content or '')
            words = [w for w in _TAG_SPLIT_RE.split(text) if w.strip()]
            rec.era_word_count = len(words)
            rec.era_reading_time_minutes = (
                max(1, round(len(words) / _WORDS_PER_MINUTE)) if words else 0
            )

    @api.depends('era_excerpt', 'content', 'subtitle')
    def _compute_era_excerpt_effective(self):
        for rec in self:
            if rec.era_excerpt and rec.era_excerpt.strip():
                rec.era_excerpt_effective = rec.era_excerpt.strip()
                continue
            sub = getattr(rec, 'subtitle', False)
            if sub:
                rec.era_excerpt_effective = sub
                continue
            text = self._strip_html_to_text(rec.content or '')
            rec.era_excerpt_effective = (
                text[:200] + ('…' if len(text) > 200 else '')
            ) if text else ''

    @api.depends('era_series_id', 'era_series_sequence')
    def _compute_era_series_neighbors(self):
        for rec in self:
            if not rec.era_series_id:
                rec.era_series_prev_id = False
                rec.era_series_next_id = False
                continue
            siblings = rec.era_series_id.post_ids.sorted(
                key=lambda p: (p.era_series_sequence, p.id)
            )
            ids = siblings.ids
            try:
                idx = ids.index(rec.id)
            except ValueError:
                rec.era_series_prev_id = False
                rec.era_series_next_id = False
                continue
            rec.era_series_prev_id = siblings[idx - 1].id if idx > 0 else False
            rec.era_series_next_id = (
                siblings[idx + 1].id if idx + 1 < len(ids) else False
            )

    @api.depends('tag_ids', 'era_series_id', 'era_series_sequence',
                 'is_published', 'published_date')
    def _compute_era_related_posts(self):
        for rec in self:
            rec.era_related_post_ids = self._pick_related_posts(rec)

    @api.depends('content')
    def _compute_era_toc_html(self):
        for rec in self:
            rec.era_toc_html = self._build_toc_html(rec.content or '')

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    @staticmethod
    def _strip_html_to_text(html):
        if not html:
            return ''
        try:
            doc = lxml_html.fragment_fromstring(html, create_parent='div')
            return ' '.join(doc.text_content().split())
        except Exception:  # noqa: BLE001
            return re.sub(r'<[^>]+>', ' ', html)

    def _pick_related_posts(self, post):
        """Up to 4 related posts. Algorithm:

          1. Up to 2 from the same series (excluding self).
          2. Fill remaining slots by descending shared-tag count.
          3. Tie-break by published_date desc, then id desc.
          4. Always exclude unpublished posts.
        """
        if not post or not post.id:
            return self.env['blog.post']

        BlogPost = self.env['blog.post'].sudo()
        related = BlogPost.browse()

        if post.era_series_id:
            series_siblings = BlogPost.search([
                ('era_series_id', '=', post.era_series_id.id),
                ('id', '!=', post.id),
                ('is_published', '=', True),
            ], limit=2, order='era_series_sequence asc, id desc')
            related |= series_siblings

        if len(related) >= 4:
            return related[:4]

        if post.tag_ids:
            tag_ids = post.tag_ids.ids
            candidates = BlogPost.search([
                ('id', '!=', post.id),
                ('id', 'not in', related.ids),
                ('is_published', '=', True),
                ('tag_ids', 'in', tag_ids),
            ], limit=20, order='published_date desc, id desc')
            scored = sorted(
                candidates,
                key=lambda p: (
                    -len(set(p.tag_ids.ids) & set(tag_ids)),
                    -(p.published_date.toordinal() if p.published_date else 0),
                    -p.id,
                ),
            )
            need = 4 - len(related)
            related |= BlogPost.browse([p.id for p in scored[:need]])

        return related[:4]

    def _build_toc_html(self, content_html):
        """Build a nested-list TOC from h2 / h3 elements in ``content_html``.

        Injects ``id`` attributes on the headings (slugified from text) so the
        anchors resolve. Returns an empty string when no headings are present.
        """
        if not content_html:
            return ''
        try:
            doc = lxml_html.fragment_fromstring(content_html, create_parent='div')
        except Exception:  # noqa: BLE001
            return ''

        headings = doc.xpath('.//h2 | .//h3')
        if not headings:
            return ''

        items = []
        for h in headings:
            text = (h.text_content() or '').strip()
            if not text:
                continue
            anchor = h.get('id') or slugify(text)[:80] or 'section'
            if not h.get('id'):
                h.set('id', anchor)
            items.append((h.tag, text, anchor))

        if not items:
            return ''

        parts = ['<ul class="o_era_blog_toc">']
        h3_open = False
        for tag, text, anchor in items:
            if tag == 'h2':
                if h3_open:
                    parts.append('</ul></li>')
                    h3_open = False
                parts.append(f'<li><a href="#{anchor}">{text}</a></li>')
            else:  # h3
                if parts and parts[-1].endswith('</li>'):
                    last = parts[-1]
                    parts[-1] = last[:-5] + '<ul>'
                    h3_open = True
                elif not h3_open:
                    parts.append('<li><ul>')
                    h3_open = True
                parts.append(f'<li><a href="#{anchor}">{text}</a></li>')
        if h3_open:
            parts.append('</ul></li>')
        parts.append('</ul>')

        return Markup(''.join(parts))

    # ------------------------------------------------------------------------
    # Mixin override
    # ------------------------------------------------------------------------

    def _get_seo_path(self):
        self.ensure_one()
        return self.website_url or '/'

    def get_seo_meta_dict(self):
        self.ensure_one()
        meta = super().get_seo_meta_dict()
        if not meta.get('description') and self.era_excerpt_effective:
            meta['description'] = self.era_excerpt_effective
            meta['og_description'] = meta.get('og_description') or self.era_excerpt_effective
        if self.era_canonical_external_url:
            meta['canonical'] = self.era_canonical_external_url
        meta['og_type'] = 'article'
        return meta

    # ------------------------------------------------------------------------
    # Auto-attach JSON-LD schemas
    # ------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        posts = super().create(vals_list)
        posts._sync_era_default_schemas()
        try:
            posts._sync_era_hreflang_entries()
        except Exception as exc:  # noqa: BLE001
            _logger.warning('blog.post.create: hreflang sync skipped (%s)', exc)
        return posts

    def write(self, vals):
        result = super().write(vals)
        # Refresh FAQ schema when FAQs or content fields change.
        if any(k in vals for k in ('era_faq_ids', 'name', 'seo_title')):
            self._sync_era_default_schemas()
        # Refresh hreflang on slug / blog change (affects website_url).
        if any(k in vals for k in ('name', 'blog_id', 'website_url')):
            try:
                self._sync_era_hreflang_entries()
            except Exception as exc:  # noqa: BLE001
                _logger.warning('blog.post.write: hreflang sync skipped (%s)', exc)
        return result

    def unlink(self):
        """Cascade-delete polymorphic FK rows before the post is removed."""
        if 'era.seo.schema.instance' in self.env:
            self.env['era.seo.schema.instance'].sudo().search([
                ('res_model', '=', 'blog.post'),
                ('res_id', 'in', self.ids),
            ]).unlink()
        self._cleanup_era_hreflang()
        return super().unlink()

    def _sync_era_default_schemas(self):
        """Attach BlogPosting + BreadcrumbList unconditionally; attach
        FAQPage iff the post carries at least one ``era.blog.faq`` row.

        Idempotent: re-running creates nothing new when instances already
        exist, and removes the FAQ instance if the post's last FAQ was
        deleted. Safe to call from create/write/onchange.
        """
        if not self:
            return
        Template = self.env['era.seo.schema.template'].sudo()
        Instance = self.env['era.seo.schema.instance'].sudo()

        templates_by_code = {
            t.code: t for t in Template.search(
                [('code', 'in', list(_AUTO_SCHEMA_CODES) + [_FAQ_SCHEMA_CODE])]
            )
        }

        for post in self.sudo():
            # Unconditional auto-attach.
            for code in _AUTO_SCHEMA_CODES:
                tpl = templates_by_code.get(code)
                if not tpl:
                    continue
                self._ensure_schema_instance(post, tpl, Instance)
            # FAQPage: attach when FAQs exist, remove when they don't.
            faq_tpl = templates_by_code.get(_FAQ_SCHEMA_CODE)
            if faq_tpl:
                if post.era_faq_ids:
                    inst = self._ensure_schema_instance(post, faq_tpl, Instance)
                    inst.write({'data_json': post._build_faq_data_json()})
                else:
                    self._remove_schema_instance(post, faq_tpl, Instance)

    @staticmethod
    def _ensure_schema_instance(post, template, Instance):
        """Return the existing instance or create one. Idempotent."""
        existing = Instance.search([
            ('res_model', '=', 'blog.post'),
            ('res_id', '=', post.id),
            ('template_id', '=', template.id),
        ], limit=1)
        if existing:
            return existing
        return Instance.create({
            'template_id': template.id,
            'res_model': 'blog.post',
            'res_id': post.id,
            'active': True,
        })

    @staticmethod
    def _remove_schema_instance(post, template, Instance):
        Instance.search([
            ('res_model', '=', 'blog.post'),
            ('res_id', '=', post.id),
            ('template_id', '=', template.id),
        ]).unlink()

    def _build_faq_data_json(self):
        """Render the post's FAQs as a JSON dict for the FAQ schema instance.

        Output shape (matches schema.org FAQPage.mainEntity):
            {"faq_main_entity": [
                {"@type": "Question",
                 "name": "Q text",
                 "acceptedAnswer": {"@type": "Answer", "text": "A text"}},
                ...
            ]}
        """
        import json
        self.ensure_one()
        items = []
        for faq in self.era_faq_ids.filtered(lambda f: f.active).sorted(
            key=lambda f: (f.sequence, f.id)
        ):
            answer_text = self._strip_html_to_text(faq.answer or '')
            items.append({
                '@type': 'Question',
                'name': faq.question or '',
                'acceptedAnswer': {
                    '@type': 'Answer',
                    'text': answer_text,
                },
            })
        return json.dumps({'faq_main_entity': items}, ensure_ascii=False)
