import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class EraSeoMixin(models.AbstractModel):
    _name = 'era.seo.mixin'
    _description = 'ERA SEO Mixin'

    # --- Title and description -------------------------------------------------

    seo_title = fields.Char(
        string='SEO Title',
        translate=True,
        help='Overrides <title>. Recommended ≤60 chars.',
    )
    seo_title_length = fields.Integer(
        string='Title Length',
        compute='_compute_seo_lengths',
        store=False,
    )
    seo_description = fields.Text(
        string='Meta Description',
        translate=True,
        help='Recommended 140–160 chars.',
    )
    seo_description_length = fields.Integer(
        string='Description Length',
        compute='_compute_seo_lengths',
        store=False,
    )
    seo_keywords = fields.Char(
        string='Meta Keywords',
        translate=True,
        help='Comma-separated. Most search engines ignore this; included for legacy.',
    )

    # --- Open Graph ------------------------------------------------------------

    seo_og_title = fields.Char(string='OG Title', translate=True)
    seo_og_description = fields.Text(string='OG Description', translate=True)
    seo_og_image = fields.Binary(string='OG Image', attachment=True)
    seo_og_image_url = fields.Char(
        string='OG Image URL',
        compute='_compute_og_image_url',
        store=True,
    )
    seo_og_type = fields.Selection(
        [
            ('website', 'Website'),
            ('article', 'Article'),
            ('product', 'Product'),
            ('profile', 'Profile'),
        ],
        string='OG Type',
        default='website',
    )

    # --- Twitter Card ----------------------------------------------------------

    seo_twitter_card = fields.Selection(
        [
            ('summary', 'Summary'),
            ('summary_large_image', 'Summary Large Image'),
            ('app', 'App'),
            ('player', 'Player'),
        ],
        string='Twitter Card',
        default='summary_large_image',
    )
    seo_twitter_site = fields.Char(string='Twitter @site')
    seo_twitter_creator = fields.Char(string='Twitter @creator')

    # --- Indexing controls -----------------------------------------------------

    seo_canonical_url = fields.Char(string='Canonical URL Override')
    seo_robots_index = fields.Boolean(string='Index this page', default=True)
    seo_robots_follow = fields.Boolean(string='Follow links on this page', default=True)
    seo_robots_archive = fields.Boolean(string='Allow archive', default=True)
    seo_robots_snippet = fields.Boolean(string='Allow snippet', default=True)

    # --- Sitemap ---------------------------------------------------------------

    seo_sitemap_include = fields.Boolean(string='Include in sitemap', default=True)
    seo_sitemap_priority = fields.Selection(
        [
            ('0.1', '0.1'), ('0.2', '0.2'), ('0.3', '0.3'), ('0.4', '0.4'),
            ('0.5', '0.5 (default)'), ('0.6', '0.6'), ('0.7', '0.7'),
            ('0.8', '0.8'), ('0.9', '0.9'), ('1.0', '1.0 (home)'),
        ],
        string='Sitemap Priority',
        default='0.5',
    )
    seo_sitemap_changefreq = fields.Selection(
        [
            ('always', 'Always'), ('hourly', 'Hourly'), ('daily', 'Daily'),
            ('weekly', 'Weekly'), ('monthly', 'Monthly'),
            ('yearly', 'Yearly'), ('never', 'Never'),
        ],
        string='Change Frequency',
        default='weekly',
    )

    # --- Schema ----------------------------------------------------------------

    seo_schema_instance_ids = fields.One2many(
        'era.seo.schema.instance',
        compute='_compute_schema_instances',
        string='JSON-LD Schemas',
    )

    # --- Computes --------------------------------------------------------------

    @api.depends('seo_title', 'seo_description')
    def _compute_seo_lengths(self):
        for rec in self:
            rec.seo_title_length = len(rec.seo_title or '')
            rec.seo_description_length = len(rec.seo_description or '')

    @api.depends('seo_og_image')
    def _compute_og_image_url(self):
        for rec in self:
            if rec.seo_og_image:
                rec.seo_og_image_url = f'/web/image/{rec._name}/{rec.id}/seo_og_image'
            else:
                rec.seo_og_image_url = False

    @api.depends()
    def _compute_schema_instances(self):
        Schema = self.env['era.seo.schema.instance']
        for rec in self:
            rec.seo_schema_instance_ids = Schema.search([
                ('res_model', '=', rec._name),
                ('res_id', '=', rec.id),
            ])

    # --- Public API ------------------------------------------------------------

    def get_seo_url(self):
        """Return canonical absolute URL of the record. Override per model."""
        self.ensure_one()
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', default='')
        return '{}{}'.format(base, self._get_seo_path())

    def _get_seo_path(self):
        """Override in subclasses. Default: root path."""
        return '/'

    def get_seo_meta_dict(self):
        """Return a dict ready for QWeb template consumption."""
        self.ensure_one()
        return {
            'title': self.seo_title,
            'description': self.seo_description,
            'keywords': self.seo_keywords,
            'og_title': self.seo_og_title or self.seo_title,
            'og_description': self.seo_og_description or self.seo_description,
            'og_image': self.seo_og_image_url,
            'og_type': self.seo_og_type,
            'twitter_card': self.seo_twitter_card,
            'twitter_site': self.seo_twitter_site,
            'twitter_creator': self.seo_twitter_creator,
            'canonical': self.seo_canonical_url or self.get_seo_url(),
            'robots': self._get_robots_directive(),
        }

    def _get_robots_directive(self):
        """Build the robots meta content string from per-field flags."""
        self.ensure_one()
        parts = []
        parts.append('index' if self.seo_robots_index else 'noindex')
        parts.append('follow' if self.seo_robots_follow else 'nofollow')
        if not self.seo_robots_archive:
            parts.append('noarchive')
        if not self.seo_robots_snippet:
            parts.append('nosnippet')
        return ', '.join(parts)

    # --- Hreflang auto-sync (Phase 6) ----------------------------------------

    def _sync_era_hreflang_entries(self):
        """Upsert ``era.seo.hreflang`` rows for every active website language.

        For each record in self:
          - Look up the websites that publish the record (websites with the
            record's website_id or all websites when the record is unscoped).
          - For each active language on each website, compute the absolute URL
            with the language URL prefix and upsert a hreflang row.
          - Mark the website's default-lang row as x-default.
          - Skip rows that are flagged is_manual so admins can override.
          - Remove stale rows for languages no longer active.

        Idempotent. Safe to call from create/write/migration/post-init.
        """
        if not self:
            return
        if 'era.seo.hreflang' not in self.env:
            return

        Hreflang = self.env['era.seo.hreflang'].sudo()
        ICP = self.env['ir.config_parameter'].sudo()
        base = (ICP.get_param('web.base.url') or '').rstrip('/')

        for rec in self:
            if not rec.id:
                continue
            languages, default_lang = rec._era_hreflang_languages()
            if not languages:
                continue

            path = rec._get_seo_path() or '/'
            seen_lang_ids = []

            for lang in languages:
                seen_lang_ids.append(lang.id)
                lang_path = rec._era_hreflang_path_for(path, lang, default_lang)
                computed_url = base + lang_path
                is_default = (default_lang and lang.id == default_lang.id)

                existing = Hreflang.search([
                    ('res_model', '=', rec._name),
                    ('res_id', '=', rec.id),
                    ('lang_id', '=', lang.id),
                ], limit=1)

                if existing:
                    if existing.is_manual:
                        # Respect manual edits, but keep the x-default flag in
                        # sync with the website's default-lang setting so the
                        # admin's URL still appears as x-default if needed.
                        if existing.is_xdefault != is_default:
                            existing.write({'is_xdefault': is_default})
                        continue
                    vals_update = {}
                    if existing.url != computed_url:
                        vals_update['url'] = computed_url
                    if existing.is_xdefault != is_default:
                        vals_update['is_xdefault'] = is_default
                    if not existing.active:
                        vals_update['active'] = True
                    if vals_update:
                        existing.write(vals_update)
                else:
                    Hreflang.create({
                        'res_model': rec._name,
                        'res_id': rec.id,
                        'lang_id': lang.id,
                        'url': computed_url,
                        'is_xdefault': is_default,
                        'is_manual': False,
                        'active': True,
                    })

            # Prune entries for languages no longer on the website.
            stale = Hreflang.search([
                ('res_model', '=', rec._name),
                ('res_id', '=', rec.id),
                ('lang_id', 'not in', seen_lang_ids),
                ('is_manual', '=', False),
            ])
            if stale:
                stale.unlink()

    def _era_hreflang_languages(self):
        """Resolve the language set for hreflang generation.

        Default implementation: prefer the host record's ``website_id``
        languages when scoped to one website; else use every active website's
        union of languages.

        Returns ``(languages_recordset, default_lang_recordset_or_False)``.
        """
        self.ensure_one()
        Lang = self.env['res.lang']
        website = False
        if 'website_id' in self._fields and self.website_id:
            website = self.website_id
        if website:
            languages = website.language_ids
            default_lang = website.default_lang_id
            return languages, default_lang

        websites = self.env['website'].sudo().search([])
        languages = websites.mapped('language_ids').sorted('id')
        default_lang = websites[:1].default_lang_id if websites else Lang.browse()
        return languages, default_lang

    def _era_hreflang_path_for(self, path, lang, default_lang):
        """Apply the language URL prefix to ``path``.

        Odoo convention: the default language has no prefix; other languages
        prepend ``/<lang.url_code>``. ``lang.url_code`` is the field that
        holds the value Odoo actually uses in routing (``ar``, ``en``, …).
        Falls back to no prefix when the lang has no url_code (e.g. the
        site's default language is configured without one).
        """
        if not path or not path.startswith('/'):
            path = '/' + (path or '')
        if default_lang and lang.id == default_lang.id:
            return path
        prefix = (lang.url_code or '').strip('/')
        if not prefix:
            return path
        return '/' + prefix + path

    def _cleanup_era_hreflang(self):
        """Remove hreflang rows pointing at these records. Call from unlink()."""
        if 'era.seo.hreflang' not in self.env:
            return
        Hreflang = self.env['era.seo.hreflang'].sudo()
        rows = Hreflang.search([
            ('res_model', '=', self._name),
            ('res_id', 'in', self.ids),
        ])
        if rows:
            rows.unlink()
