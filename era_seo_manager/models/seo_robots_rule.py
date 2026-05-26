from odoo import fields, models


class EraSeoRobotsRule(models.Model):
    _name = 'era.seo.robots.rule'
    _description = 'Robots.txt Rule'

    name = fields.Char(string='Name')
    active = fields.Boolean(default=True)
