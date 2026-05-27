from odoo import fields, models


class EraContentBlock(models.Model):
    _name = 'era.content.block'
    _description = 'Reusable Content Block'

    name = fields.Char(string='Name', required=True)
    active = fields.Boolean(default=True)
