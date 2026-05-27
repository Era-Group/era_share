"""ERA SEO Blog — author profile.

Standalone author entity so non-staff authors can have a published
profile without being internal users. Optionally links to a `res.users`
record when the author IS staff.

Per `era_seo_manager` SPEC §11.7 (extracted to this addon).
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from ..utils import slugify

_logger = logging.getLogger(__name__)


class EraBlogAuthor(models.Model):
    _name = 'era.blog.author'
    _description = 'Blog Author Profile'
    _inherit = ['era.seo.mixin']
    _order = 'sequence, name'

    name = fields.Char(string='Name', required=True)
    slug = fields.Char(
        string='Slug',
        required=True,
        index=True,
        copy=False,
        help='URL-safe identifier. Appears in /blog/author/<slug>.',
    )
    user_id = fields.Many2one(
        'res.users',
        string='Linked User',
        ondelete='set null',
        help='Optional link to an internal user. Allows the author to be '
             'auto-credited when they create blog posts logged in.',
    )
    bio = fields.Html(string='Bio', translate=True, sanitize=True)
    avatar = fields.Binary(string='Avatar', attachment=True)
    avatar_url = fields.Char(compute='_compute_avatar_url', store=True)
    email = fields.Char(string='Email')
    role = fields.Char(string='Role / Title', translate=True,
                       help='E.g. "Senior Engineer", "Editor-in-Chief".')

    social_twitter = fields.Char(string='X (Twitter) URL')
    social_linkedin = fields.Char(string='LinkedIn URL')
    social_github = fields.Char(string='GitHub URL')
    social_website = fields.Char(string='Personal Website')

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    post_ids = fields.One2many(
        'blog.post',
        'era_author_profile_id',
        string='Posts',
    )
    post_count = fields.Integer(compute='_compute_post_count')

    _slug_unique = models.Constraint(
        'UNIQUE(slug)',
        'Author slug must be unique.',
    )

    @api.depends('post_ids')
    def _compute_post_count(self):
        for rec in self:
            rec.post_count = len(rec.post_ids)

    @api.depends('avatar')
    def _compute_avatar_url(self):
        for rec in self:
            if rec.avatar and rec.id:
                rec.avatar_url = f'/web/image/era.blog.author/{rec.id}/avatar'
            else:
                rec.avatar_url = False

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

    @api.onchange('user_id')
    def _onchange_user_id(self):
        if self.user_id and not self.email:
            self.email = self.user_id.email

    def _get_seo_path(self):
        self.ensure_one()
        return f'/blog/author/{self.slug}' if self.slug else '/blog'
