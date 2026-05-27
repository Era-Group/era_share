from odoo import fields, models


class EraSeoHreflang(models.Model):
    _name = 'era.seo.hreflang'
    _description = 'SEO Hreflang Entry'

    name = fields.Char(string='Name')
    active = fields.Boolean(default=True)
