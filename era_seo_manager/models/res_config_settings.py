import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# ir.config_parameter keys used to persist settings.
_PARAM_PREFIX = 'era_seo.'


class ResConfigSettings(models.TransientModel):
    """ERA SEO settings section inside Website Settings.

    All values are persisted via ir.config_parameter so they survive
    module upgrades and are readable without loading the transient model.

    Per SPEC §16.
    """

    _inherit = 'res.config.settings'

    # --- Organization identity ------------------------------------------------

    era_seo_default_organization_name = fields.Char(
        string='Organization Name',
        config_parameter='era_seo.organization_name',
    )
    era_seo_default_legal_name = fields.Char(
        string='Legal Name',
        config_parameter='era_seo.legal_name',
    )
    era_seo_default_logo_url = fields.Char(
        string='Logo URL',
        config_parameter='era_seo.logo_url',
        help='Absolute URL to the organization logo image.',
    )
    era_seo_default_og_image_url = fields.Char(
        string='Default OG Image URL',
        config_parameter='era_seo.default_og_image_url',
    )
    era_seo_default_twitter_handle = fields.Char(
        string='Twitter Handle',
        config_parameter='era_seo.twitter_handle',
        help='Must start with @, e.g. @mycompany.',
    )

    # --- Social profiles (used in Organization sameAs) -----------------------

    era_seo_social_facebook = fields.Char(
        string='Facebook URL',
        config_parameter='era_seo.social_facebook',
    )
    era_seo_social_twitter = fields.Char(
        string='Twitter URL',
        config_parameter='era_seo.social_twitter',
    )
    era_seo_social_linkedin = fields.Char(
        string='LinkedIn URL',
        config_parameter='era_seo.social_linkedin',
    )
    era_seo_social_instagram = fields.Char(
        string='Instagram URL',
        config_parameter='era_seo.social_instagram',
    )
    era_seo_social_youtube = fields.Char(
        string='YouTube URL',
        config_parameter='era_seo.social_youtube',
    )

    # --- Verification codes ---------------------------------------------------

    era_seo_google_site_verification = fields.Char(
        string='Google Site Verification',
        config_parameter='era_seo.google_site_verification',
    )
    era_seo_bing_site_verification = fields.Char(
        string='Bing Site Verification',
        config_parameter='era_seo.bing_site_verification',
    )

    # --- Engine toggle --------------------------------------------------------

    era_seo_schema_engine_enabled = fields.Boolean(
        string='Enable JSON-LD Schema Engine',
        config_parameter='era_seo.schema_engine_enabled',
        default=True,
    )

    # --- Validation -----------------------------------------------------------

    @api.constrains('era_seo_default_twitter_handle')
    def _check_twitter_handle(self):
        for rec in self:
            handle = rec.era_seo_default_twitter_handle
            if handle and not handle.startswith('@'):
                raise ValidationError(
                    _('Twitter handle must start with @, e.g. @mycompany.')
                )


class ResCompany(models.Model):
    """Helper on res.company for schema rendering context.

    Per SPEC §8 / Step 5.
    """

    _inherit = 'res.company'

    def get_era_seo_social_profiles_json(self):
        """Return a JSON array of social profile URLs for this company.

        Reads the ERA SEO social settings from ir.config_parameter and
        returns only non-blank entries, formatted as a JSON array suitable
        for schema.org ``sameAs``.
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        profile_keys = [
            'era_seo.social_facebook',
            'era_seo.social_twitter',
            'era_seo.social_linkedin',
            'era_seo.social_instagram',
            'era_seo.social_youtube',
        ]
        profiles = [ICP.get_param(k, '') for k in profile_keys]
        return json.dumps([p for p in profiles if p])

    def get_era_seo_logo_url(self):
        """Return the logo URL from settings, falling back to company logo attachment."""
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        url = ICP.get_param('era_seo.logo_url', '')
        if url:
            return url
        base = ICP.get_param('web.base.url', '')
        return '{}/web/image/res.company/{}/logo'.format(base, self.id) if base else ''
