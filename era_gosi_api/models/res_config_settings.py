# -*- coding: utf-8 -*-
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gosi_registration_number = fields.Char(
        string='GOSI Registration Number',
        config_parameter='era_gosi.registration_number',
        help='Establishment registration number used in the GOSI API path.',
    )
    gosi_client_id = fields.Char(
        string='GOSI Client ID',
        config_parameter='era_gosi.client_id',
        help='Client ID issued by GOSI for the engagement deduction API.',
    )
    gosi_client_secret = fields.Char(
        string='GOSI Client Secret',
        config_parameter='era_gosi.client_secret',
        help='Client secret issued by GOSI for the engagement deduction API.',
    )
    gosi_private_key = fields.Char(
        string='GOSI Private Key (PEM)',
        size=8192,
        config_parameter='era_gosi.private_key',
        help='Private key in PEM format used to sign DPoP JWTs.',
    )
    gosi_private_key_password = fields.Char(
        string='GOSI Private Key Password',
        config_parameter='era_gosi.private_key_password',
        help='Password for the private key if it is encrypted. Leave empty for unencrypted keys.',
    )
    gosi_timeout_seconds = fields.Integer(
        string='GOSI Timeout (Seconds)',
        default=300,
        config_parameter='era_gosi.timeout_seconds',
        help='Request timeout in seconds for calls to the GOSI API.',
    )
    gosi_proxy_url = fields.Char(
        string='GOSI Proxy URL',
        default='https://app.era.net.sa',
        config_parameter='era_gosi.proxy_url',
        help='Base URL of the KSA Odoo proxy (e.g., https://app.era.net.sa).',
    )
    gosi_proxy_token = fields.Char(
        string='GOSI Proxy Token',
        config_parameter='era_gosi.proxy_token',
        help='Shared token to send in X-GOSI-Proxy-Token header when using the proxy.',
    )

    def action_gosi_test_connection(self):
        self.ensure_one()
        self.env['gosi.client'].sudo().get_access_token()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('GOSI'),
                'message': _('Access token retrieved successfully.'),
                'type': 'success',
            },
        }
