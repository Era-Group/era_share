from odoo import fields, models


class EraSeoAuditRun(models.Model):
    _name = 'era.seo.audit.run'
    _description = 'SEO Audit Run'

    name = fields.Char(string='Name')
    active = fields.Boolean(default=True)
