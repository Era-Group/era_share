from odoo import fields, models


class EraSeoAuditFinding(models.Model):
    _name = 'era.seo.audit.finding'
    _description = 'SEO Audit Finding'

    name = fields.Char(string='Name')
    active = fields.Boolean(default=True)
