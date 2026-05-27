from odoo import fields, models


class EraSeoRedirect(models.Model):
    _name = 'era.seo.redirect'
    _description = 'SEO Redirect'

    name = fields.Char(string='Name', compute='_compute_name')
    active = fields.Boolean(default=True)

    def _compute_name(self):
        for rec in self:
            rec.name = rec.id
