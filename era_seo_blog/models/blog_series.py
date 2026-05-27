"""ERA SEO Blog — series model.

A *series* groups a sequence of blog posts that belong to one narrative
arc. Each post has a position within the series; the series itself has a
landing page at ``/blog/series/<slug>``.

Per `era_seo_manager` SPEC §11.5 (extracted to this addon).
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..utils import slugify

_logger = logging.getLogger(__name__)


class EraBlogSeries(models.Model):
    _name = 'era.blog.series'
    _description = 'Blog Series'
    _inherit = ['era.seo.mixin']
    _order = 'sequence, name'

    name = fields.Char(string='Name', required=True, translate=True)
    slug = fields.Char(
        string='Slug',
        required=True,
        index=True,
        copy=False,
        help='URL-safe identifier. Appears in /blog/series/<slug>.',
    )
    description = fields.Html(string='Description', translate=True, sanitize=True)
    cover_image = fields.Binary(string='Cover Image', attachment=True)
    cover_image_url = fields.Char(
        string='Cover Image URL',
        compute='_compute_cover_image_url',
        store=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer(default=0)

    post_ids = fields.One2many(
        'blog.post',
        'era_series_id',
        string='Posts',
    )
    post_count = fields.Integer(
        string='Post Count',
        compute='_compute_post_count',
    )

    _slug_unique = models.Constraint(
        'UNIQUE(slug)',
        'Series slug must be unique.',
    )

    @api.depends('post_ids')
    def _compute_post_count(self):
        for rec in self:
            rec.post_count = len(rec.post_ids)

    @api.depends('cover_image')
    def _compute_cover_image_url(self):
        for rec in self:
            if rec.cover_image and rec.id:
                rec.cover_image_url = f'/web/image/era.blog.series/{rec.id}/cover_image'
            else:
                rec.cover_image_url = False

    @api.constrains('slug')
    def _check_slug_format(self):
        for rec in self:
            if not rec.slug:
                continue
            normalized = slugify(rec.slug)
            if normalized != rec.slug:
                raise ValidationError(
                    _('Slug must be URL-safe (lowercase letters, digits, hyphens). '
                      'Suggested: %s', normalized)
                )

    @api.onchange('name')
    def _onchange_name(self):
        if self.name and not self.slug:
            self.slug = slugify(self.name)

    def _get_seo_path(self):
        self.ensure_one()
        return f'/blog/series/{self.slug}' if self.slug else '/blog'
