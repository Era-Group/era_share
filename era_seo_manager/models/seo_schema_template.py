from odoo import fields, models


class EraSeoSchemaTemplate(models.Model):
    _name = 'era.seo.schema.template'
    _description = 'SEO Schema Template'

    name = fields.Char(string='Name', required=True)
    active = fields.Boolean(default=True)
