"""ERA SEO Manager — Status Overview wizard.

Opens a read-only form that shows a snapshot of everything the module has
configured and the current state of SEO data across the site.

Accessible from Website > SEO > Overview.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_SOCIAL_KEYS = [
    'era_seo.social_facebook',
    'era_seo.social_twitter',
    'era_seo.social_linkedin',
    'era_seo.social_instagram',
    'era_seo.social_youtube',
]


class EraSeoStatus(models.TransientModel):
    """Read-only status overview for ERA SEO Manager."""

    _name = 'era.seo.status'
    _description = 'ERA SEO — Status Overview'

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    org_name = fields.Char(string='Organization Name', readonly=True)
    legal_name = fields.Char(string='Legal Name', readonly=True)
    logo_url = fields.Char(string='Logo URL', readonly=True)
    twitter_handle = fields.Char(string='Twitter Handle', readonly=True)
    google_verification = fields.Char(string='Google Site Verification', readonly=True)
    bing_verification = fields.Char(string='Bing Site Verification', readonly=True)
    schema_engine_enabled = fields.Boolean(string='Schema Engine Enabled', readonly=True)
    social_profiles_set = fields.Integer(string='Social Profiles Configured', readonly=True)

    # Computed booleans for traffic-light display
    has_org_name = fields.Boolean(readonly=True)
    has_logo = fields.Boolean(readonly=True)
    has_twitter = fields.Boolean(readonly=True)
    has_google_verification = fields.Boolean(readonly=True)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    total_pages = fields.Integer(string='Total Pages', readonly=True)
    published_pages = fields.Integer(string='Published Pages', readonly=True)
    pages_with_seo_title = fields.Integer(string='Pages with SEO Title', readonly=True)
    pages_with_seo_description = fields.Integer(string='Pages with SEO Description', readonly=True)
    pages_noindex = fields.Integer(string='Noindex Pages', readonly=True)
    pages_with_canonical = fields.Integer(string='Pages with Custom Canonical', readonly=True)

    # ------------------------------------------------------------------
    # Schema engine
    # ------------------------------------------------------------------
    templates_total = fields.Integer(string='Schema Templates', readonly=True)
    templates_active = fields.Integer(string='Active Templates', readonly=True)
    instances_total = fields.Integer(string='Schema Instances', readonly=True)
    instances_active = fields.Integer(string='Active Instances', readonly=True)

    # ------------------------------------------------------------------
    # Redirects
    # ------------------------------------------------------------------
    redirects_total = fields.Integer(string='Redirects', readonly=True)
    redirects_active = fields.Integer(string='Active Redirects', readonly=True)

    # ------------------------------------------------------------------
    # Hreflang
    # ------------------------------------------------------------------
    hreflang_rules = fields.Integer(string='Hreflang Rules', readonly=True)

    # ------------------------------------------------------------------
    # Module version
    # ------------------------------------------------------------------
    module_version = fields.Char(string='Module Version', readonly=True)

    # ------------------------------------------------------------------
    # Default / compute
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, field_names):
        """Populate all stats at wizard open time."""
        vals = super().default_get(field_names)
        ICP = self.env['ir.config_parameter'].sudo()

        # --- Settings ---
        org_name = ICP.get_param('era_seo.organization_name', '')
        logo = ICP.get_param('era_seo.logo_url', '')
        twitter = ICP.get_param('era_seo.twitter_handle', '')
        google_v = ICP.get_param('era_seo.google_site_verification', '')
        bing_v = ICP.get_param('era_seo.bing_site_verification', '')
        schema_on = ICP.get_param('era_seo.schema_engine_enabled', 'True') not in ('False', '0', '')
        socials_set = sum(1 for k in _SOCIAL_KEYS if ICP.get_param(k, ''))

        vals.update({
            'org_name': org_name or _('(not set)'),
            'legal_name': ICP.get_param('era_seo.legal_name', '') or _('(not set)'),
            'logo_url': logo or _('(not set)'),
            'twitter_handle': twitter or _('(not set)'),
            'google_verification': google_v or _('(not set)'),
            'bing_verification': bing_v or _('(not set)'),
            'schema_engine_enabled': schema_on,
            'social_profiles_set': socials_set,
            'has_org_name': bool(org_name),
            'has_logo': bool(logo),
            'has_twitter': bool(twitter),
            'has_google_verification': bool(google_v),
        })

        # --- Module version ---
        mod = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'era_seo_manager'), ('state', '=', 'installed')], limit=1
        )
        vals['module_version'] = mod.installed_version if mod else _('unknown')

        # --- Pages ---
        Page = self.env['website.page'].sudo()
        all_pages = Page.search([])
        vals['total_pages'] = len(all_pages)
        vals['published_pages'] = len(all_pages.filtered('is_published'))
        vals['pages_with_seo_title'] = len(
            all_pages.filtered(lambda p: bool(p.seo_title))
        )
        vals['pages_with_seo_description'] = len(
            all_pages.filtered(lambda p: bool(p.seo_description))
        )
        vals['pages_noindex'] = len(
            all_pages.filtered(lambda p: not p.seo_robots_index)
        )
        vals['pages_with_canonical'] = len(
            all_pages.filtered(lambda p: bool(p.seo_canonical_url))
        )

        # --- Schema ---
        Tpl = self.env['era.seo.schema.template'].sudo()
        Inst = self.env['era.seo.schema.instance'].sudo()
        vals['templates_total'] = Tpl.search_count([('active', 'in', [True, False])])
        vals['templates_active'] = Tpl.search_count([('active', '=', True)])
        vals['instances_total'] = Inst.search_count([('active', 'in', [True, False])])
        vals['instances_active'] = Inst.search_count([('active', '=', True)])

        # --- Redirects ---
        try:
            Redir = self.env['era.seo.redirect'].sudo()
            vals['redirects_total'] = Redir.search_count([('active', 'in', [True, False])])
            vals['redirects_active'] = Redir.search_count([('active', '=', True)])
        except Exception:
            vals['redirects_total'] = 0
            vals['redirects_active'] = 0

        # --- Hreflang ---
        try:
            vals['hreflang_rules'] = self.env['era.seo.hreflang'].sudo().search_count([])
        except Exception:
            vals['hreflang_rules'] = 0

        return vals

    def action_go_settings(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/odoo/settings?searchTerms=ERA SEO',
            'target': 'self',
        }
