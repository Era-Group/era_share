from odoo import fields, models


class CsUserGuide(models.Model):
    _name = 'cs.user.guide'
    _description = 'Customer Success User Guide'
    _order = 'id'

    name = fields.Char(required=True, readonly=True)
    release = fields.Char(readonly=True)
    updated_on = fields.Date(readonly=True)
    content = fields.Html(readonly=True, sanitize=False)
