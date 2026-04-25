# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    yusr_geofence_enabled = fields.Boolean(
        string='Enforce Geofencing',
        config_parameter='era_yusr_api.geofence_enabled',
        default=True,
    )
    yusr_geofence_radius = fields.Integer(
        string='Geofence Radius (meters)',
        config_parameter='era_yusr_api.geofence_radius',
        default=200,
    )
