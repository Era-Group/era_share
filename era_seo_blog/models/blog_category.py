"""ERA SEO Blog — category model (taxonomic, hierarchical).

Categories are one-per-post (taxonomic) and form a tree via parent_id.
Distinct from `blog.tag` which is folksonomic and multi-per-post.

Per `era_seo_manager` SPEC §11.6 (extracted to this addon).
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import slugify

_logger = logging.getLogger(__name__)


class EraBlogCategory(models.Model):
    _name = 'era.blog.category'
    _description = 'Blog Category'
    _inherit = ['era.seo.mixin']
    _order = 'parent_path, sequence, name'
    _parent_store = True
    _parent_name = 'parent_id'

    name = fields.Char(string='Name', required=True, translate=True)
    slug = fields.Char(
        string='Slug',
        required=True,
        index=True,
        copy=False,
        help='URL-safe identifier. Appears in /blog/category/<slug>.',
    )
    parent_id = fields.Many2one(
        'era.blog.category',
        string='Parent Category',
        ondelete='set null',
        index=True,
    )
    parent_path = fields.Char(index=True, unaccent=False)
    child_ids = fields.One2many('era.blog.category', 'parent_id', string='Children')
    description = fields.Html(string='Description', translate=True, sanitize=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer(default=0)

    post_ids = fields.One2many(
        'blog.post',
        'era_category_id',
        string='Posts',
    )
    post_count = fields.Integer(compute='_compute_post_count')

    _slug_unique = models.Constraint(
        'UNIQUE(slug)',
        'Category slug must be unique.',
    )

    @api.depends('post_ids')
    def _compute_post_count(self):
        for rec in self:
            rec.post_count = len(rec.post_ids)

    @api.constrains('parent_id')
    def _check_no_cycle(self):
        if not self._check_recursion():
            raise ValidationError(_('A category cannot be its own ancestor.'))

    @api.constrains('slug')
    def _check_slug_format(self):
        for rec in self:
            if not rec.slug:
                continue
            normalized = slugify(rec.slug)
            if normalized != rec.slug:
                raise ValidationError(
                    _('Slug must be URL-safe. Suggested: %s', normalized)
                )

    @api.onchange('name')
    def _onchange_name(self):
        if self.name and not self.slug:
            self.slug = slugify(self.name)

    def _get_seo_path(self):
        self.ensure_one()
        return f'/blog/category/{self.slug}' if self.slug else '/blog'
