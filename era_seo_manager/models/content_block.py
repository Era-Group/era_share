"""ERA SEO — reusable, schema-aware content block (SPEC §14.1).

An admin-editable QWeb partial that the website-builder snippets (FAQ, CTA,
breadcrumbs, …) and any ``t-call`` can reuse. When a ``schema_template_id``
is set, the block can auto-attach the matching JSON-LD schema to its host.

Inherits ``era.seo.mixin`` so it carries the same SEO metadata surface as
pages/posts (useful when a block is promoted to a standalone fragment).
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

BLOCK_TYPES = [
    ('faq', 'FAQ'),
    ('cta', 'Call to Action'),
    ('author_box', 'Author Box'),
    ('related_posts', 'Related Posts'),
    ('breadcrumbs', 'Breadcrumbs'),
    ('feature_grid', 'Feature Grid'),
    ('pricing_table', 'Pricing Table'),
    ('testimonial', 'Testimonial'),
    ('custom', 'Custom'),
]


class EraContentBlock(models.Model):
    _name = 'era.content.block'
    _inherit = ['era.seo.mixin']
    _description = 'Reusable Content Block'
    _order = 'name'

    name = fields.Char(string='Name', required=True, translate=True)
    code = fields.Char(
        string='Code',
        required=True,
        help='Programmatic key for QWeb t-call / snippet reference. Unique.',
    )
    block_type = fields.Selection(
        BLOCK_TYPES,
        string='Block Type',
        default='custom',
        required=True,
    )
    content_html = fields.Html(
        string='Content',
        translate=True,
        sanitize=False,
        help='The block body. Rendered as-is inside the snippet wrapper.',
    )
    schema_template_id = fields.Many2one(
        'era.seo.schema.template',
        string='Schema Template',
        ondelete='set null',
        help='If set, rendering this block can auto-attach this JSON-LD '
             'schema to the host record.',
    )
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint(
        'UNIQUE(code)',
        'Content block code must be unique.',
    )

    @api.constrains('code')
    def _check_code(self):
        for rec in self:
            cleaned = (rec.code or '').replace('_', '').replace('-', '')
            if rec.code and not cleaned.isalnum():
                raise ValidationError(
                    _('Code may contain only letters, digits, "-" and "_": %s',
                      rec.code))

    def _get_seo_path(self):
        # Content blocks are fragments, not standalone URLs; the mixin still
        # needs a path, so return a stable virtual one.
        self.ensure_one()
        return '/content-block/%s' % (self.code or self.id)
