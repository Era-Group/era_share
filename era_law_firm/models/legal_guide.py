"""The manual, inside the product.

A guide that lives in a file nobody opens is not a guide. These are records:
searchable, grouped by the moment you need them, readable on the same screen
as the work — and writable by a legal manager, so a firm can add its own house
rules beside the shipped ones instead of keeping a separate document that
drifts out of date.

Shipped topics carry `is_shipped`, which is what makes an upgrade able to
refresh them without touching anything the firm wrote itself.
"""
from odoo import fields, models


class LegalGuideTopic(models.Model):
    _name = 'legal.guide.topic'
    _description = 'User Guide Topic'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True,
                       help="The question this topic answers.")
    sequence = fields.Integer(default=10)
    category = fields.Selection([
        ('start', 'Getting Started'),
        ('daily', 'Daily Work'),
        ('money', 'Money'),
        ('outside', 'Client and AI'),
        ('reference', 'Reference'),
    ], required=True, default='daily', help="Where this topic sits in the guide.")
    audience = fields.Selection([
        ('all', 'Everyone'),
        ('lawyer', 'Lawyers'),
        ('supervisor', 'Supervisors and managers'),
        ('accountant', 'Accounting'),
    ], default='all', help="Who most needs this topic. It stays readable by all staff.")
    icon = fields.Char(default='fa-book', help="Font Awesome class shown on the card.")
    summary = fields.Char(translate=True, help="One line, shown on the card.")
    body = fields.Html(translate=True, sanitize=False,
                       help="The topic itself: steps, a worked example, and what good practice looks like.")
    is_shipped = fields.Boolean(
        default=False, readonly=True,
        help="Shipped with the module. A firm's own topics are left alone by upgrades.")
