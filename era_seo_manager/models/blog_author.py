from odoo import fields, models


class EraBlogAuthor(models.Model):
    _name = 'era.blog.author'
    _description = 'Blog Author Profile'

    name = fields.Char(string='Name', required=True)
    active = fields.Boolean(default=True)
