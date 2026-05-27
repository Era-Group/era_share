"""ERA SEO — audit runner.

One ``era.seo.audit.run`` row per scheduled or manual scan. Each run
iterates the registered checks (the ``_check_*`` methods on this model)
and records findings in ``era.seo.audit.finding``.

Per SPEC §13.
"""
import logging
import re
import threading
from collections import Counter, defaultdict

from lxml import html as lxml_html

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Thread-local storage for the current run's seen-finding key set.
# Odoo ORM recordsets reject arbitrary attribute assignment, so we carry
# this mutable state here instead of on ``self``.
_run_local = threading.local()

# Title/description length thresholds (chars). Per SPEC §13.2.
_TITLE_TOO_LONG = 60
_TITLE_TOO_SHORT = 20
_DESC_TOO_LONG = 160
_DESC_TOO_SHORT = 70

# Slug rules.
_SLUG_TOO_LONG = 75
_SLUG_STOPWORDS = {
    'en': {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
           'for', 'of', 'with', 'by'},
    'ar': {'في', 'من', 'إلى', 'على', 'عن', 'مع'},
}

# Content thresholds.
_THIN_CONTENT_WORDS = 300

# Redirect chain depth.
_REDIRECT_CHAIN_MAX = 3


class EraSeoAuditRun(models.Model):
    _name = 'era.seo.audit.run'
    _description = 'SEO Audit Run'
    _order = 'date_started desc, id desc'
    _rec_name = 'name'

    name = fields.Char(string='Name', compute='_compute_name', store=True)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('running', 'Running'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        default='draft',
        required=True,
        readonly=True,
    )
    date_started = fields.Datetime(string='Started', readonly=True)
    date_finished = fields.Datetime(string='Finished', readonly=True)
    website_id = fields.Many2one(
        'website',
        string='Website',
        ondelete='set null',
        help='Optional. When set, the audit scans only this website. '
             'Empty = every website.',
    )
    pages_scanned = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)

    finding_ids = fields.One2many(
        'era.seo.audit.finding',
        'run_id',
        string='Findings',
    )

    critical_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    warning_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    info_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    total_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    unresolved_count = fields.Integer(
        compute='_compute_counts', store=True,
    )

    # --- Computes ---------------------------------------------------------

    @api.depends('date_started', 'state')
    def _compute_name(self):
        for rec in self:
            stamp = (rec.date_started or fields.Datetime.now()).strftime('%Y-%m-%d %H:%M')
            rec.name = 'Audit {}'.format(stamp)

    @api.depends('finding_ids', 'finding_ids.severity', 'finding_ids.is_resolved')
    def _compute_counts(self):
        for rec in self:
            findings = rec.finding_ids
            rec.critical_count = len(findings.filtered(lambda f: f.severity == 'critical'))
            rec.warning_count = len(findings.filtered(lambda f: f.severity == 'warning'))
            rec.info_count = len(findings.filtered(lambda f: f.severity == 'info'))
            rec.total_count = len(findings)
            rec.unresolved_count = len(findings.filtered(lambda f: not f.is_resolved))

    # ------------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------------

    def action_run(self):
        """Run the audit synchronously. Best for manual button clicks."""
        for rec in self:
            rec._run_audit()
        return True

    @api.model
    def run_scheduled_audit(self, website_id=None):
        """Cron entry: create a fresh run and execute it. Returns the run."""
        vals = {'state': 'draft'}
        if website_id:
            vals['website_id'] = website_id
        run = self.create(vals)
        run._run_audit()
        return run

    def _run_audit(self):
        """Execute every ``_check_*`` method on this model against ``self``.

        Wrapped in a savepoint so a single check failing doesn't abort the
        rest. Each check is a Python method that creates 0..n finding rows
        on ``self``.
        """
        self.ensure_one()
        self.write({
            'state': 'running',
            'date_started': fields.Datetime.now(),
            'pages_scanned': 0,
            'error_message': False,
        })

        # Track the (check_code, res_model, res_id) keys detected this run so
        # we can (a) upsert instead of duplicating and (b) auto-resolve
        # findings on scanned pages that no longer occur.
        _run_local.seen_finding_keys = set()

        try:
            pages = self._scope_pages()
            self.write({'pages_scanned': len(pages)})
            for check_method in self._get_check_methods():
                try:
                    with self.env.cr.savepoint():
                        check_method(pages)
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        'audit.run %d: check %s failed: %s',
                        self.id, check_method.__name__, exc,
                    )
            self._auto_resolve_fixed(pages)
            self.write({
                'state': 'done',
                'date_finished': fields.Datetime.now(),
            })
        except Exception as exc:  # noqa: BLE001
            _logger.exception('audit.run %d: aborted with error', self.id)
            self.write({
                'state': 'failed',
                'date_finished': fields.Datetime.now(),
                'error_message': str(exc),
            })

    def _auto_resolve_fixed(self, pages):
        """Mark unresolved findings on scanned pages that were NOT re-detected.

        If a page was scanned this run and a previously-open finding for it
        is no longer in ``_seen_finding_keys``, the issue has been fixed —
        resolve it (preserving history) rather than leaving a stale row.
        Scoped to the pages actually scanned so other websites / unscanned
        records are never touched.
        """
        Finding = self.env['era.seo.audit.finding'].sudo()
        if not pages:
            return
        open_findings = Finding.search([
            ('is_resolved', '=', False),
            ('res_model', '=', 'website.page'),
            ('res_id', 'in', pages.ids),
        ])
        stale = open_findings.filtered(
            lambda f: (f.check_code, f.res_model, f.res_id) not in getattr(_run_local, 'seen_finding_keys', set())
        )
        if stale:
            stale.write({
                'is_resolved': True,
                'resolved_date': fields.Datetime.now(),
                'resolved_user_id': self.env.user.id,
            })

    # ------------------------------------------------------------------------
    # Scoping + check registry
    # ------------------------------------------------------------------------

    def _scope_pages(self):
        """Return the recordset of pages to audit (honours website_id scope)."""
        Page = self.env['website.page'].sudo()
        domain = []
        if self.website_id:
            domain.append(('website_id', '=', self.website_id.id))
        return Page.search(domain)

    def _get_check_methods(self):
        """List the bound ``_check_*`` methods to run, in registration order."""
        names = [
            '_check_missing_seo_title',
            '_check_title_length',
            '_check_duplicate_seo_title',
            '_check_missing_meta_description',
            '_check_description_length',
            '_check_duplicate_meta_description',
            '_check_missing_og_image',
            '_check_missing_canonical',
            '_check_noindex_in_sitemap',
            '_check_missing_h1',
            '_check_multiple_h1',
            '_check_image_missing_alt',
            '_check_slug_length',
            '_check_slug_uppercase',
            '_check_slug_stopwords',
            '_check_missing_schema',
            '_check_thin_content',
            '_check_orphan_page',
            '_check_redirect_chain',
            '_check_redirect_loop',
        ]
        return [getattr(self, name) for name in names if hasattr(self, name)]

    # ------------------------------------------------------------------------
    # Finding helper
    # ------------------------------------------------------------------------

    def _add_finding(self, page, severity, code, name, details=None, suggested=None):
        """Upsert one finding keyed by (check_code, res_model, res_id).

        Re-detecting the same defect updates the existing row (refreshes
        severity/name/details, re-points run_id at this run, and reopens it
        if it had been resolved) instead of creating a duplicate. Only
        genuinely new defects create a new row. AI-fill fields on the
        existing finding are preserved.
        """
        Finding = self.env['era.seo.audit.finding'].sudo()
        # Remember we saw this key so _auto_resolve_fixed leaves it alone.
        seen = getattr(_run_local, 'seen_finding_keys', None)
        if seen is not None:
            seen.add((code, page._name, page.id))

        vals = {
            'run_id': self.id,
            'severity': severity,
            'check_name': name,
            'url': page.url or '/',
            'details': details or '',
            'suggested_fix': suggested or '',
        }
        existing = Finding.search([
            ('check_code', '=', code),
            ('res_model', '=', page._name),
            ('res_id', '=', page.id),
        ], limit=1)
        if existing:
            if existing.is_resolved:
                vals.update({
                    'is_resolved': False,
                    'resolved_date': False,
                    'resolved_user_id': False,
                })
            existing.write(vals)
        else:
            vals.update({
                'check_code': code,
                'res_model': page._name,
                'res_id': page.id,
            })
            Finding.create(vals)

    # ------------------------------------------------------------------------
    # Checks — title / description
    # ------------------------------------------------------------------------

    def _check_missing_seo_title(self, pages):
        for p in pages.filtered(lambda r: not r.seo_title):
            self._add_finding(
                p, 'critical', 'missing_seo_title', 'Missing SEO Title',
                details='The page has no seo_title and falls back to website name.',
                suggested='Add a concise (~50 char) title in the SEO tab.',
            )

    def _check_title_length(self, pages):
        for p in pages.filtered(lambda r: r.seo_title):
            n = len(p.seo_title)
            if n > _TITLE_TOO_LONG:
                self._add_finding(
                    p, 'warning', 'title_too_long',
                    'SEO Title Too Long ({} chars)'.format(n),
                    details='SERP truncates beyond ~60 chars.',
                    suggested='Shorten the title to {} chars or fewer.'.format(_TITLE_TOO_LONG),
                )
            elif n < _TITLE_TOO_SHORT:
                self._add_finding(
                    p, 'info', 'title_too_short',
                    'SEO Title Too Short ({} chars)'.format(n),
                    details='Short titles miss keyword opportunities.',
                    suggested='Expand the title toward 50-60 chars with descriptive terms.',
                )

    def _check_duplicate_seo_title(self, pages):
        groups = defaultdict(list)
        for p in pages.filtered('seo_title'):
            groups[p.seo_title].append(p)
        for title, dupes in groups.items():
            if len(dupes) > 1:
                for p in dupes:
                    others = ', '.join(d.url or '/' for d in dupes if d != p)
                    self._add_finding(
                        p, 'critical', 'duplicate_seo_title',
                        'Duplicate SEO Title',
                        details='Same title also used on: {}'.format(others),
                        suggested='Make each page title unique.',
                    )

    def _check_missing_meta_description(self, pages):
        for p in pages.filtered(lambda r: not r.seo_description):
            self._add_finding(
                p, 'critical', 'missing_meta_description', 'Missing Meta Description',
                suggested='Add a 140-160 char description in the SEO tab.',
            )

    def _check_description_length(self, pages):
        for p in pages.filtered('seo_description'):
            n = len(p.seo_description)
            if n > _DESC_TOO_LONG:
                self._add_finding(
                    p, 'warning', 'description_too_long',
                    'Meta Description Too Long ({} chars)'.format(n),
                    suggested='Trim to {} chars or fewer.'.format(_DESC_TOO_LONG),
                )
            elif n < _DESC_TOO_SHORT:
                self._add_finding(
                    p, 'info', 'description_too_short',
                    'Meta Description Too Short ({} chars)'.format(n),
                    suggested='Expand toward 140-160 chars.',
                )

    def _check_duplicate_meta_description(self, pages):
        groups = defaultdict(list)
        for p in pages.filtered('seo_description'):
            groups[p.seo_description].append(p)
        for desc, dupes in groups.items():
            if len(dupes) > 1:
                for p in dupes:
                    others = ', '.join(d.url or '/' for d in dupes if d != p)
                    self._add_finding(
                        p, 'warning', 'duplicate_meta_description',
                        'Duplicate Meta Description',
                        details='Same description also used on: {}'.format(others),
                        suggested='Make each description unique.',
                    )

    # ------------------------------------------------------------------------
    # Checks — OG / canonical / indexing
    # ------------------------------------------------------------------------

    def _check_missing_og_image(self, pages):
        for p in pages.filtered(lambda r: not (r.seo_og_image or r.seo_og_image_url)):
            self._add_finding(
                p, 'warning', 'missing_og_image', 'Missing OG Image',
                suggested='Upload an OG image (recommended 1200x630) in the SEO tab.',
            )

    def _check_missing_canonical(self, pages):
        # The mixin's get_seo_url() always returns SOMETHING, so the only
        # meaningful "missing" case is when the configured base_url is empty.
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if base:
            return
        for p in pages:
            if not p.seo_canonical_url:
                self._add_finding(
                    p, 'info', 'missing_canonical', 'Cannot Resolve Canonical',
                    details='web.base.url is empty and no canonical override is set.',
                    suggested='Set web.base.url in system parameters.',
                )

    def _check_noindex_in_sitemap(self, pages):
        for p in pages:
            if p.seo_sitemap_include and not p.seo_robots_index:
                self._add_finding(
                    p, 'critical', 'noindex_in_sitemap',
                    'Noindex Page Included in Sitemap',
                    details='Page is set to noindex but is_sitemap_include is True.',
                    suggested='Either re-enable indexing or exclude from sitemap.',
                )

    # ------------------------------------------------------------------------
    # Checks — content HTML
    # ------------------------------------------------------------------------

    def _check_missing_h1(self, pages):
        for p in pages:
            doc = self._content_doc(p)
            if doc is None:
                continue
            if not doc.xpath('//h1'):
                self._add_finding(
                    p, 'critical', 'missing_h1', 'Missing <h1>',
                    suggested='Add exactly one H1 to the page content.',
                )

    def _check_multiple_h1(self, pages):
        for p in pages:
            doc = self._content_doc(p)
            if doc is None:
                continue
            h1s = doc.xpath('//h1')
            if len(h1s) > 1:
                self._add_finding(
                    p, 'warning', 'multiple_h1',
                    'Multiple <h1> Tags ({})'.format(len(h1s)),
                    suggested='Keep only one H1; convert others to H2.',
                )

    def _check_image_missing_alt(self, pages):
        for p in pages:
            doc = self._content_doc(p)
            if doc is None:
                continue
            imgs = doc.xpath('//img')
            missing = [img for img in imgs if not (img.get('alt') or '').strip()]
            if missing:
                self._add_finding(
                    p, 'warning', 'image_missing_alt',
                    '{} Image(s) Without alt'.format(len(missing)),
                    details='{} of {} <img> elements have no alt attribute.'.format(
                        len(missing), len(imgs)),
                    suggested='Add descriptive alt text to every image.',
                )

    def _check_thin_content(self, pages):
        for p in pages:
            text = self._content_text(p)
            words = len(text.split())
            if 0 < words < _THIN_CONTENT_WORDS:
                self._add_finding(
                    p, 'warning', 'thin_content',
                    'Thin Content ({} words)'.format(words),
                    details='Pages with fewer than {} words tend to rank poorly.'.format(
                        _THIN_CONTENT_WORDS),
                    suggested='Expand the content with more depth on the topic.',
                )

    # ------------------------------------------------------------------------
    # Checks — slug
    # ------------------------------------------------------------------------

    def _check_slug_length(self, pages):
        for p in pages:
            slug = (p.url or '').strip('/').split('/')[-1]
            if len(slug) > _SLUG_TOO_LONG:
                self._add_finding(
                    p, 'info', 'slug_too_long',
                    'Slug Too Long ({} chars)'.format(len(slug)),
                    suggested='Shorten the URL slug below {} chars.'.format(_SLUG_TOO_LONG),
                )

    def _check_slug_uppercase(self, pages):
        for p in pages:
            slug = (p.url or '').strip('/').split('/')[-1]
            if any(c.isupper() for c in slug):
                self._add_finding(
                    p, 'info', 'slug_contains_uppercase',
                    'Slug Contains Uppercase',
                    suggested='Use lowercase letters and hyphens in URL slugs.',
                )

    def _check_slug_stopwords(self, pages):
        stopwords = _SLUG_STOPWORDS['en'] | _SLUG_STOPWORDS.get('ar', set())
        for p in pages:
            slug = (p.url or '').strip('/').split('/')[-1].lower()
            tokens = re.split(r'[\-_/]+', slug)
            hit = [t for t in tokens if t in stopwords]
            if hit:
                self._add_finding(
                    p, 'info', 'slug_contains_stopwords',
                    'Slug Contains Stopwords ({})'.format(', '.join(hit)),
                    suggested='Drop stopwords from the slug to keep it lean.',
                )

    # ------------------------------------------------------------------------
    # Checks — schema / internal links / redirects
    # ------------------------------------------------------------------------

    def _check_missing_schema(self, pages):
        if 'era.seo.schema.instance' not in self.env:
            return
        Instance = self.env['era.seo.schema.instance'].sudo()
        for p in pages:
            n = Instance.search_count([
                ('res_model', '=', p._name),
                ('res_id', '=', p.id),
                ('active', '=', True),
            ])
            if n == 0:
                self._add_finding(
                    p, 'warning', 'missing_schema', 'No JSON-LD Schema Attached',
                    suggested='Attach an Article / Service / Product schema instance '
                              'from the page form\'s Schemas tab.',
                )

    def _check_orphan_page(self, pages):
        """A page is an orphan when no other page's content links to it.

        Cheap implementation: scan every page's content for href="<this.url>".
        """
        urls_by_id = {p.id: (p.url or '').strip() for p in pages if (p.url or '').strip()}
        # Build content index once.
        contents = {p.id: (p.content or p.arch or '') for p in pages}
        for p in pages:
            url = urls_by_id.get(p.id)
            if not url or url == '/':
                # Homepage cannot be an orphan; skip.
                continue
            referenced = False
            for other_id, content in contents.items():
                if other_id == p.id:
                    continue
                if url in content:
                    referenced = True
                    break
            if not referenced:
                self._add_finding(
                    p, 'warning', 'orphan_page', 'Orphan Page',
                    details='No other audited page contains a link to {}.'.format(url),
                    suggested='Link to this page from a relevant navigation or related '
                              'content block so crawlers can discover it.',
                )

    def _check_redirect_chain(self, pages):
        if 'era.seo.redirect' not in self.env:
            return
        Redirect = self.env['era.seo.redirect'].sudo()
        active = Redirect.search([('is_active', '=', True), ('is_regex', '=', False)])
        by_source = {r.source_url: r for r in active}
        for r in active:
            chain = [r.source_url]
            current = r
            seen = {r.source_url}
            depth = 0
            while current and current.target_url in by_source:
                next_r = by_source[current.target_url]
                if next_r.source_url in seen:
                    break  # loop, caught by _check_redirect_loop
                chain.append(next_r.source_url)
                seen.add(next_r.source_url)
                current = next_r
                depth += 1
                if depth > _REDIRECT_CHAIN_MAX:
                    break
            if depth > _REDIRECT_CHAIN_MAX:
                self._add_finding(
                    r, 'critical', 'broken_redirect_chain',
                    'Redirect Chain Too Long ({} hops)'.format(depth),
                    details='Chain: {}'.format(' -> '.join(chain)),
                    suggested='Collapse the chain so each redirect points to the final URL.',
                )

    def _check_redirect_loop(self, pages):
        if 'era.seo.redirect' not in self.env:
            return
        Redirect = self.env['era.seo.redirect'].sudo()
        active = Redirect.search([('is_active', '=', True), ('is_regex', '=', False)])
        by_source = {r.source_url: r for r in active}
        for r in active:
            seen = {r.source_url}
            current = r
            while current and current.target_url in by_source:
                next_r = by_source[current.target_url]
                if next_r.source_url in seen:
                    self._add_finding(
                        r, 'critical', 'redirect_loop', 'Redirect Loop Detected',
                        details='Loop: {} -> ... -> {}'.format(
                            r.source_url, next_r.source_url),
                        suggested='Remove the cycle. The dispatch hook returns 508 '
                                  'when this hits visitors.',
                    )
                    break
                seen.add(next_r.source_url)
                current = next_r

    # ------------------------------------------------------------------------
    # Content extraction helpers
    # ------------------------------------------------------------------------

    @staticmethod
    def _content_doc(page):
        """Return an lxml fragment for the page's content, or None."""
        # website.page uses arch_db (delegated through view); blog.post has
        # `content`. Try both.
        html_text = getattr(page, 'content', None) or getattr(page, 'arch', None)
        if not html_text:
            return None
        try:
            return lxml_html.fragment_fromstring(html_text, create_parent='div')
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _content_text(cls, page):
        doc = cls._content_doc(page)
        if doc is None:
            return ''
        return ' '.join(doc.text_content().split())
