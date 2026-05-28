"""ERA GSC — settings (Website → Configuration → Settings → ERA SEO — GSC)."""
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_gsc_client_id = fields.Char(
        string='OAuth Client ID',
        config_parameter='era_seo_suite.client_id',
        help='Create an OAuth 2.0 Client (Web application) in Google Cloud '
             'Console, enable the Search Console API, and paste the client id '
             'here. Authorized redirect URIs must include '
             '<base>/era_gsc/oauth/callback.',
    )
    era_gsc_client_secret = fields.Char(
        string='OAuth Client Secret',
        config_parameter='era_seo_suite.client_secret',
        help='From the same Google Cloud OAuth 2.0 Client.',
    )
    era_gsc_pull_window_days = fields.Integer(
        string='Pull Window (days)',
        config_parameter='era_seo_suite.pull_window_days',
        default=28,
        help='How many days of search analytics each Pull fetches '
             '(GSC data is ~2 days delayed).',
    )
    era_gsc_redirect_uri = fields.Char(
        string='Authorized Redirect URI',
        compute='_compute_era_gsc_redirect_uri',
        readonly=True,
        help='Add this exact URL to the OAuth Client\'s '
             '"Authorized redirect URIs" in Google Cloud Console.',
    )

    @api.depends('era_gsc_client_id')   # arbitrary trigger; value is global
    def _compute_era_gsc_redirect_uri(self):
        base = (self.env['ir.config_parameter'].sudo()
                .get_param('web.base.url') or '').rstrip('/')
        uri = (base + '/era_gsc/oauth/callback') if base \
            else '/era_gsc/oauth/callback'
        for rec in self:
            rec.era_gsc_redirect_uri = uri
