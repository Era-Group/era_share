from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_address_server_url = fields.Char(
        string='Address Lookup Server URL',
        config_parameter='era_address_client.server_url',
        help='URL of the server running the era_address_lookup module (e.g. https://service.era.net.sa)',
    )
