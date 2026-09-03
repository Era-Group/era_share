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
    # The AI is a tool in the lawyer's hand and the portal is the client's
    # window; one section for both read as if the assistant belonged to the
    # client. 'outside' was that section: rows still carrying it fall back to
    # the default rather than blocking the upgrade.
    category = fields.Selection([
        ('start', 'Getting Started'),
        ('daily', 'Daily Work'),
        ('ai', 'AI for the Lawyer'),
        ('money', 'Money'),
        ('portal', 'The Client'),
        ('reference', 'Reference'),
    ], required=True, default='daily', ondelete={'outside': 'set default'},
        group_expand='_expand_categories',
        help="Where this topic sits in the guide.")

    def _expand_categories(self, categories, domain):
        """Read the guide in the order it was written.

        Grouping a selection field orders the columns by the stored value, so
        the sections came out alphabetical by their technical keys — Getting
        Started last, which is the one place a new reader starts. Naming the
        order here also keeps an empty section visible, so a firm that has not
        written its own topics still sees where they would go.
        """
        return [key for key, _label in self._fields['category'].selection]
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
