# -*- coding: utf-8 -*-
from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'


    url = fields.Char(string='Base URL', config_parameter='era_muqeem_client.url')
    user_name = fields.Char(string='User Name', config_parameter='era_muqeem_client.user_name')
    user_pass = fields.Char(string='User Pass', config_parameter='era_muqeem_client.user_pass')
    user_app_id = fields.Char(string='App ID', config_parameter='era_muqeem_client.user_app_id')
    user_app_key = fields.Char(string='App Key', config_parameter='era_muqeem_client.user_app_key')
    user_x_integrator_id = fields.Char(
        string='X Integrator ID',
        config_parameter='era_muqeem_client.user_x_integrator_id',
    )
    user_environment = fields.Selection(
        [('sandbox', 'Sandbox'), ('production', 'Production')],
        config_parameter='era_muqeem_client.user_environment',
    )



