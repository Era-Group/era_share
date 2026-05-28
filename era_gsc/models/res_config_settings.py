"""ERA GSC — settings (Website → Configuration → Settings → ERA SEO — GSC)."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_gsc_client_id = fields.Char(
        string='OAuth Client ID',
        config_parameter='era_gsc.client_id',
        help='Create an OAuth 2.0 Client (Web application) in Google Cloud '
             'Console, enable the Search Console API, and paste the client id '
             'here. Authorized redirect URIs must include '
             '<base>/era_gsc/oauth/callback.',
    )
    era_gsc_client_secret = fields.Char(
        string='OAuth Client Secret',
        config_parameter='era_gsc.client_secret',
        help='From the same Google Cloud OAuth 2.0 Client.',
    )
    era_gsc_pull_window_days = fields.Integer(
        string='Pull Window (days)',
        config_parameter='era_gsc.pull_window_days',
        default=28,
        help='How many days of search analytics each Pull fetches '
             '(GSC data is ~2 days delayed).',
    )
