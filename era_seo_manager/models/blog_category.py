from odoo import fields, models


class EraBlogCategory(models.Model):
    _name = 'era.blog.category'
    _description = 'Blog Category'

    name = fields.Char(string='Name', required=True, translate=True)
    active = fields.Boolean(default=True)
