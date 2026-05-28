"""ERA GEO — settings (Website → Configuration → Settings)."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_geo_llms_enabled = fields.Boolean(
        string='Publish /llms.txt',
        config_parameter='era_seo_suite.llms_enabled',
        default=True,
        help='Serve a Markdown site map at /llms.txt for AI answer engines '
             '(llmstxt.org convention).',
    )
    era_geo_site_summary = fields.Char(
        string='Site Summary (llms.txt)',
        config_parameter='era_seo_suite.site_summary',
        help='One-line description of the site, used as the blockquote intro '
             'in /llms.txt. Falls back to the company name when empty.',
    )
    era_geo_llms_max_items = fields.Integer(
        string='Max Items in /llms.txt',
        config_parameter='era_seo_suite.llms_max_items',
        default=100,
    )
    era_geo_llms_include_blog = fields.Boolean(
        string='Include Blog in /llms.txt',
        config_parameter='era_seo_suite.llms_include_blog',
        default=True,
    )
