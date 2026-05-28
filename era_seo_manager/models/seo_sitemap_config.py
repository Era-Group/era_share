from odoo import fields, models


class EraSeoSitemapConfig(models.Model):
    _name = 'era.seo.sitemap.config'
    _description = 'Sitemap Configuration'

    name = fields.Char(string='Name')
    active = fields.Boolean(default=True)
