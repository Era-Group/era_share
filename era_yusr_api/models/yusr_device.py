# -*- coding: utf-8 -*-
from odoo import fields, models


class YusrDevice(models.Model):
    _name = 'yusr.device'
    _description = 'Yusr Mobile Device (for push notifications)'
    _rec_name = 'device_token'

    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade', index=True,
    )
    device_token = fields.Char(string='Push Token', required=True, index=True)
    platform = fields.Selection(
        [('ios', 'iOS'), ('android', 'Android')],
        string='Platform', required=True,
    )
    app_version = fields.Char(string='App Version')
    last_seen = fields.Datetime(string='Last Seen', default=fields.Datetime.now)
    active = fields.Boolean(default=True)

    _device_token_unique = models.Constraint(
        'unique(device_token)',
        'Device token must be unique.',
    )
