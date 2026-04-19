# -*- coding: utf-8 -*-
import secrets

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    yusr_jwt_secret = fields.Char(
        string='Yusr JWT Secret',
        config_parameter='era_yusr_api.jwt_secret',
        help="Secret used to sign JWT tokens. Keep secret. "
             "Rotating this logs out all mobile users."
    )
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

    def action_generate_jwt_secret(self):
        """Generate a strong random secret."""
        self.ensure_one()
        self.yusr_jwt_secret = secrets.token_urlsafe(64)
        self.env['ir.config_parameter'].sudo().set_param(
            'era_yusr_api.jwt_secret', self.yusr_jwt_secret
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
