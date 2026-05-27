"""ERA SEO Blog — per-post FAQ entries.

Each FAQ row is a question + answer attached to a single ``blog.post``.
Rendered both as a visible accordion on the post page and as a
``FAQPage`` JSON-LD schema (eligible for rich FAQ snippets in Google
SERPs).

Per `era_seo_manager` SPEC §11.8 (extracted to this addon).
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class EraBlogFaq(models.Model):
    _name = 'era.blog.faq'
    _description = 'Blog Post FAQ Entry'
    _order = 'post_id, sequence, id'

    post_id = fields.Many2one(
        'blog.post',
        string='Blog Post',
        required=True,
        ondelete='cascade',
        index=True,
    )
    question = fields.Char(string='Question', required=True, translate=True)
    answer = fields.Html(
        string='Answer',
        required=True,
        translate=True,
        sanitize=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    name = fields.Char(string='Name', compute='_compute_name', store=True)

    @api.depends('question')
    def _compute_name(self):
        for rec in self:
            rec.name = (rec.question or '')[:80]
