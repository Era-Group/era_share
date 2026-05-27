from odoo import fields, models


class EraBlogSeries(models.Model):
    _name = 'era.blog.series'
    _description = 'Blog Series'

    name = fields.Char(string='Name', required=True, translate=True)
    active = fields.Boolean(default=True)
