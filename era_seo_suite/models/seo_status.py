"""ERA SEO Manager — Status Overview wizard.

Opens a read-only form that shows a snapshot of everything the module has
configured and the current state of SEO data across the site.

Accessible from:
  - Website → SEO → Overview
  - Website → Configuration → Settings (ERA SEO block → "View Status Report" button)
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
_SOCIAL_LABELS = ['Facebook', 'Twitter/X', 'LinkedIn', 'Instagram', 'YouTube']


class EraSeoStatus(models.TransientModel):
    """Read-only status overview for ERA SEO Manager."""

    _name = 'era.seo.status'
    _description = 'ERA SEO — Status Overview'

    # ------------------------------------------------------------------
    # Module meta
    # ------------------------------------------------------------------
    module_version = fields.Char(string='Module Version', readonly=True)

    # ------------------------------------------------------------------
    # Organization identity
    # ------------------------------------------------------------------
    org_name = fields.Char(string='Organization Name', readonly=True)
    legal_name = fields.Char(string='Legal Name', readonly=True)
    logo_url = fields.Char(string='Logo URL', readonly=True)
    og_image_url = fields.Char(string='Default OG Image URL', readonly=True)
    twitter_handle = fields.Char(string='Twitter Handle', readonly=True)
    social_profiles_set = fields.Integer(string='Social Profiles Configured (out of 5)', readonly=True)
    social_profiles_detail = fields.Char(string='Configured Profiles', readonly=True)

    # ------------------------------------------------------------------
    # Site verification
    # ------------------------------------------------------------------
    google_verification = fields.Char(string='Google Search Console', readonly=True)
    bing_verification = fields.Char(string='Bing Webmaster Tools', readonly=True)

    # ------------------------------------------------------------------
    # JSON-LD Schema Engine
    # ------------------------------------------------------------------
    schema_engine_enabled = fields.Boolean(string='Schema Engine Enabled', readonly=True)
    templates_total = fields.Integer(string='Built-in Templates', readonly=True)
    templates_active = fields.Integer(string='Active Templates', readonly=True)
    instances_total = fields.Integer(string='Schema Instances (total)', readonly=True)
    instances_active = fields.Integer(string='Active Instances', readonly=True)

    # ------------------------------------------------------------------
    # Page SEO coverage
    # ------------------------------------------------------------------
    total_pages = fields.Integer(string='Total Pages', readonly=True)
    published_pages = fields.Integer(string='Published Pages', readonly=True)
    pages_with_seo_title = fields.Integer(string='Pages with SEO Title', readonly=True)
    pages_with_seo_description = fields.Integer(string='Pages with SEO Description', readonly=True)
    pages_with_canonical = fields.Integer(string='Pages with Custom Canonical URL', readonly=True)
    pages_noindex = fields.Integer(string='Pages set to Noindex', readonly=True)
    seo_title_pct = fields.Integer(string='SEO Title Coverage (%)', readonly=True)
    seo_desc_pct = fields.Integer(string='SEO Description Coverage (%)', readonly=True)

    # ------------------------------------------------------------------
    # Redirects & Hreflang
    # ------------------------------------------------------------------
    redirects_total = fields.Integer(string='Redirects (total)', readonly=True)
    redirects_active = fields.Integer(string='Active Redirects', readonly=True)
    hreflang_rules = fields.Integer(string='Hreflang Rules', readonly=True)

    # ------------------------------------------------------------------
    # Checklist flags (True = done)
    # ------------------------------------------------------------------
    ok_org_name = fields.Boolean(readonly=True)
    ok_logo = fields.Boolean(readonly=True)
    ok_og_image = fields.Boolean(readonly=True)
    ok_twitter = fields.Boolean(readonly=True)
    ok_google_verification = fields.Boolean(readonly=True)
    ok_bing_verification = fields.Boolean(readonly=True)
    ok_schema_engine = fields.Boolean(readonly=True)
    ok_has_instances = fields.Boolean(readonly=True)
    ok_title_coverage = fields.Boolean(readonly=True)   # >= 80 %
    ok_desc_coverage = fields.Boolean(readonly=True)    # >= 80 %

    # ------------------------------------------------------------------
    # Default / compute
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, field_names):
        """Populate all stats at wizard open time."""
        vals = super().default_get(field_names)
        ICP = self.env['ir.config_parameter'].sudo()

        # ── Module version ──────────────────────────────────────────────
        mod = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'era_seo_manager'), ('state', '=', 'installed')], limit=1
        )
        vals['module_version'] = mod.installed_version if mod else _('unknown')

        # ── Organization identity ───────────────────────────────────────
        org_name = ICP.get_param('era_seo.organization_name', '')
        legal_name = ICP.get_param('era_seo.legal_name', '')
        logo = ICP.get_param('era_seo.logo_url', '')
        og_image = ICP.get_param('era_seo.og_image_url', '')
        twitter = ICP.get_param('era_seo.twitter_handle', '')

        social_values = {
            label: ICP.get_param(key, '')
            for key, label in zip(_SOCIAL_KEYS, _SOCIAL_LABELS)
        }
        socials_set = sum(1 for v in social_values.values() if v)
        configured_labels = [lbl for lbl, v in social_values.items() if v]

        vals.update({
            'org_name': org_name or _('(not set)'),
            'legal_name': legal_name or _('(not set)'),
            'logo_url': logo or _('(not set)'),
            'og_image_url': og_image or _('(not set)'),
            'twitter_handle': twitter or _('(not set)'),
            'social_profiles_set': socials_set,
            'social_profiles_detail': ', '.join(configured_labels) if configured_labels else _('none'),
            'ok_org_name': bool(org_name),
            'ok_logo': bool(logo),
            'ok_og_image': bool(og_image),
            'ok_twitter': bool(twitter),
        })

        # ── Site verification ───────────────────────────────────────────
        google_v = ICP.get_param('era_seo.google_site_verification', '')
        bing_v = ICP.get_param('era_seo.bing_site_verification', '')
        vals.update({
            'google_verification': google_v or _('(not set)'),
            'bing_verification': bing_v or _('(not set)'),
            'ok_google_verification': bool(google_v),
            'ok_bing_verification': bool(bing_v),
        })

        # ── Schema engine ───────────────────────────────────────────────
        schema_on = ICP.get_param('era_seo.schema_engine_enabled', 'True') not in ('False', '0', '')
        Tpl = self.env['era.seo.schema.template'].sudo()
        Inst = self.env['era.seo.schema.instance'].sudo()
        inst_active = Inst.search_count([('active', '=', True)])
        vals.update({
            'schema_engine_enabled': schema_on,
            'templates_total': Tpl.search_count([('active', 'in', [True, False])]),
            'templates_active': Tpl.search_count([('active', '=', True)]),
            'instances_total': Inst.search_count([('active', 'in', [True, False])]),
            'instances_active': inst_active,
            'ok_schema_engine': schema_on,
            'ok_has_instances': inst_active > 0,
        })

        # ── Page SEO coverage ───────────────────────────────────────────
        Page = self.env['website.page'].sudo()
        all_pages = Page.search([])
        total = len(all_pages)
        with_title = len(all_pages.filtered(lambda p: bool(p.seo_title)))
        with_desc = len(all_pages.filtered(lambda p: bool(p.seo_description)))
        title_pct = round(with_title * 100 / total) if total else 0
        desc_pct = round(with_desc * 100 / total) if total else 0
        vals.update({
            'total_pages': total,
            'published_pages': len(all_pages.filtered('is_published')),
            'pages_with_seo_title': with_title,
            'pages_with_seo_description': with_desc,
            'pages_with_canonical': len(all_pages.filtered(lambda p: bool(p.seo_canonical_url))),
            'pages_noindex': len(all_pages.filtered(lambda p: not p.seo_robots_index)),
            'seo_title_pct': title_pct,
            'seo_desc_pct': desc_pct,
            'ok_title_coverage': title_pct >= 80,
            'ok_desc_coverage': desc_pct >= 80,
        })

        # ── Redirects ───────────────────────────────────────────────────
        try:
            Redir = self.env['era.seo.redirect'].sudo()
            vals['redirects_total'] = Redir.search_count([('active', 'in', [True, False])])
            vals['redirects_active'] = Redir.search_count([('active', '=', True)])
        except Exception:
            vals['redirects_total'] = 0
            vals['redirects_active'] = 0

        # ── Hreflang ────────────────────────────────────────────────────
        try:
            vals['hreflang_rules'] = self.env['era.seo.hreflang'].sudo().search_count([])
        except Exception:
            vals['hreflang_rules'] = 0

        return vals

    # ------------------------------------------------------------------
    # Quick-action buttons
    # ------------------------------------------------------------------

    def action_go_settings(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/odoo/settings?searchTerms=ERA SEO',
            'target': 'self',
        }

    def action_open_pages(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Website Pages'),
            'res_model': 'website.page',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_open_schema_templates(self):
        return self.env.ref('era_seo_suite.action_seo_schema_template').read()[0]

    def action_open_schema_instances(self):
        return self.env.ref('era_seo_suite.action_seo_schema_instance').read()[0]
