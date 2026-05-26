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
