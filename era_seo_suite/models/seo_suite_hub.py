"""ERA SEO Suite — singleton hub model.

One persistent record per database (seeded as ``hub_main``) drives the
unified dashboard form. All KPI fields are non-stored computes that read
the live state of the suite on each form open.

The Settings-tab toggles read/write directly against ``ir.config_parameter``
so the hub is the canonical place to flip the most-used flags without
leaving the screen.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_TRUE = ('True', '1', 'true', 'yes', 'on')


def _icp_get(env, key, default=''):
    return env['ir.config_parameter'].sudo().get_param(key, default)


def _icp_bool(env, key, default=False):
    val = _icp_get(env, key, 'True' if default else 'False')
    return val in _TRUE


def _icp_set_bool(env, key, value):
    env['ir.config_parameter'].sudo().set_param(key, 'True' if value else 'False')


class EraSeoSuiteHub(models.Model):
    _name = 'era.seo.suite.hub'
    _description = 'ERA SEO Suite Hub'
    _rec_name = 'name'

    name = fields.Char(default='ERA SEO Suite', readonly=True)

    # =========================================================================
    # Dashboard KPIs (non-stored — recomputed on each form open)
    # =========================================================================

    kpi_published_pages = fields.Integer(
        string='Published Pages', compute='_compute_kpis')
    kpi_active_redirects = fields.Integer(
        string='Active Redirects', compute='_compute_kpis')
    kpi_schema_instances = fields.Integer(
        string='Schema Instances', compute='_compute_kpis')
    kpi_audit_last_date = fields.Datetime(
        string='Last Audit', compute='_compute_kpis')
    kpi_audit_open_findings = fields.Integer(
        string='Open Findings', compute='_compute_kpis')
    kpi_audit_critical = fields.Integer(
        string='Critical', compute='_compute_kpis')

    # GEO
    kpi_geo_crawlers_total = fields.Integer(
        string='AI Crawlers', compute='_compute_kpis')
    kpi_geo_crawlers_blocked = fields.Integer(
        string='AI Crawlers Blocked', compute='_compute_kpis')
    kpi_geo_llms_enabled = fields.Boolean(
        string='/llms.txt published', compute='_compute_kpis')

    # GSC
    kpi_gsc_accounts_connected = fields.Integer(
        string='GSC Accounts Connected', compute='_compute_kpis')
    kpi_gsc_sites = fields.Integer(
        string='GSC Sites', compute='_compute_kpis')
    kpi_gsc_clicks_28d = fields.Integer(
        string='Clicks (28d)', compute='_compute_kpis')
    kpi_gsc_impressions_28d = fields.Integer(
        string='Impressions (28d)', compute='_compute_kpis')
    kpi_gsc_last_pull = fields.Date(
        string='GSC Last Pull', compute='_compute_kpis')

    # =========================================================================
    # Settings — every ICP-backed key the suite owns, in ONE declarative map.
    # The shared _compute_settings / _inverse_settings round-trip them through
    # ir.config_parameter, so the hub Settings tab is the canonical surface.
    # =========================================================================

    # field_name -> (icp_key, kind, default)
    # kinds: 'bool' | 'int' | 'char'
    _SETTING_MAP = {
        # ---------- Organization (era_seo_manager) ----------
        'setting_org_name':         ('era_seo.organization_name',     'char', ''),
        'setting_legal_name':       ('era_seo.legal_name',            'char', ''),
        'setting_logo_url':         ('era_seo.logo_url',              'char', ''),
        'setting_og_image_url':     ('era_seo.default_og_image_url',  'char', ''),
        'setting_twitter_handle':   ('era_seo.twitter_handle',        'char', ''),
        'setting_google_verify':    ('era_seo.google_site_verification', 'char', ''),
        'setting_bing_verify':      ('era_seo.bing_site_verification',   'char', ''),
        'setting_schema_engine':    ('era_seo.schema_engine_enabled', 'bool', True),
        # ---------- Social profiles (era_seo_manager) ----------
        'setting_social_facebook':  ('era_seo.social_facebook',       'char', ''),
        'setting_social_twitter':   ('era_seo.social_twitter',        'char', ''),
        'setting_social_linkedin':  ('era_seo.social_linkedin',       'char', ''),
        'setting_social_instagram': ('era_seo.social_instagram',      'char', ''),
        'setting_social_youtube':   ('era_seo.social_youtube',        'char', ''),
        # ---------- AI Auto-Fix (era_seo_ai) ----------
        'setting_ai_enabled':       ('era_seo.ai_enabled',            'bool', False),
        # ---------- GEO (era_geo) ----------
        'setting_llms_enabled':     ('era_geo.llms_enabled',          'bool', True),
        'setting_llms_summary':     ('era_geo.site_summary',          'char', ''),
        'setting_llms_max_items':   ('era_geo.llms_max_items',        'int', 100),
        'setting_llms_include_blog':('era_geo.llms_include_blog',     'bool', True),
        # ---------- GSC (era_gsc) ----------
        'setting_gsc_client_id':    ('era_gsc.client_id',             'char', ''),
        'setting_gsc_client_secret':('era_gsc.client_secret',         'char', ''),
        'setting_gsc_pull_window':  ('era_gsc.pull_window_days',      'int', 28),
    }

    # --- Organization
    setting_org_name = fields.Char(
        string='Organization Name', compute='_compute_settings', inverse='_inverse_settings')
    setting_legal_name = fields.Char(
        string='Legal Name', compute='_compute_settings', inverse='_inverse_settings')
    setting_logo_url = fields.Char(
        string='Logo URL', compute='_compute_settings', inverse='_inverse_settings')
    setting_og_image_url = fields.Char(
        string='Default OG Image URL', compute='_compute_settings', inverse='_inverse_settings')
    setting_twitter_handle = fields.Char(
        string='Twitter Handle', compute='_compute_settings', inverse='_inverse_settings')
    setting_google_verify = fields.Char(
        string='Google Site Verification', compute='_compute_settings', inverse='_inverse_settings')
    setting_bing_verify = fields.Char(
        string='Bing Site Verification', compute='_compute_settings', inverse='_inverse_settings')
    setting_schema_engine = fields.Boolean(
        string='Schema Engine Enabled', compute='_compute_settings', inverse='_inverse_settings')

    # --- Social
    setting_social_facebook = fields.Char(
        string='Facebook', compute='_compute_settings', inverse='_inverse_settings')
    setting_social_twitter = fields.Char(
        string='Twitter / X', compute='_compute_settings', inverse='_inverse_settings')
    setting_social_linkedin = fields.Char(
        string='LinkedIn', compute='_compute_settings', inverse='_inverse_settings')
    setting_social_instagram = fields.Char(
        string='Instagram', compute='_compute_settings', inverse='_inverse_settings')
    setting_social_youtube = fields.Char(
        string='YouTube', compute='_compute_settings', inverse='_inverse_settings')

    # --- AI
    setting_ai_enabled = fields.Boolean(
        string='AI Auto-Fix Enabled', compute='_compute_settings', inverse='_inverse_settings')
    setting_ai_agent_name = fields.Char(
        string='AI Agent (current)', compute='_compute_ai_agent_name', readonly=True)

    # --- GEO
    setting_llms_enabled = fields.Boolean(
        string='Publish /llms.txt', compute='_compute_settings', inverse='_inverse_settings')
    setting_llms_summary = fields.Char(
        string='Site Summary (llms.txt)', compute='_compute_settings', inverse='_inverse_settings')
    setting_llms_max_items = fields.Integer(
        string='Max Items in /llms.txt', compute='_compute_settings', inverse='_inverse_settings')
    setting_llms_include_blog = fields.Boolean(
        string='Include Blog in /llms.txt', compute='_compute_settings', inverse='_inverse_settings')

    # --- GSC
    setting_gsc_client_id = fields.Char(
        string='GSC OAuth Client ID', compute='_compute_settings', inverse='_inverse_settings')
    setting_gsc_client_secret = fields.Char(
        string='GSC OAuth Client Secret', compute='_compute_settings', inverse='_inverse_settings')
    setting_gsc_pull_window = fields.Integer(
        string='GSC Pull Window (days)', compute='_compute_settings', inverse='_inverse_settings')
    setting_gsc_redirect_uri = fields.Char(
        string='GSC Authorized Redirect URI',
        compute='_compute_gsc_redirect_uri', readonly=True)

    # =========================================================================
    # Computes
    # =========================================================================

    def _compute_kpis(self):
        Page = self.env['website.page'].sudo()
        Redirect = self.env.get('era.seo.redirect')
        Instance = self.env.get('era.seo.schema.instance')
        Run = self.env.get('era.seo.audit.run')
        Finding = self.env.get('era.seo.audit.finding')
        Crawler = self.env.get('era.geo.ai.crawler')
        GscAccount = self.env.get('era.gsc.account')
        GscSite = self.env.get('era.gsc.site')
        GscQuery = self.env.get('era.gsc.query')

        from datetime import date, timedelta
        d28 = date.today() - timedelta(days=28)

        for rec in self:
            rec.kpi_published_pages = Page.search_count(
                [('website_published', '=', True)])
            rec.kpi_active_redirects = Redirect.sudo().search_count(
                [('is_active', '=', True)]) if Redirect is not None else 0
            rec.kpi_schema_instances = Instance.sudo().search_count(
                [('active', '=', True)]) if Instance is not None else 0

            last_run = Run.sudo().search(
                [('state', '=', 'done')], order='date_finished desc', limit=1
            ) if Run is not None else False
            rec.kpi_audit_last_date = last_run.date_finished if last_run else False
            rec.kpi_audit_open_findings = Finding.sudo().search_count(
                [('is_resolved', '=', False)]) if Finding is not None else 0
            rec.kpi_audit_critical = Finding.sudo().search_count(
                [('is_resolved', '=', False),
                 ('severity', '=', 'critical')]) if Finding is not None else 0

            rec.kpi_geo_crawlers_total = Crawler.sudo().search_count(
                []) if Crawler is not None else 0
            rec.kpi_geo_crawlers_blocked = Crawler.sudo().search_count(
                [('allowed', '=', False)]) if Crawler is not None else 0
            rec.kpi_geo_llms_enabled = _icp_bool(
                self.env, 'era_geo.llms_enabled', True)

            rec.kpi_gsc_accounts_connected = GscAccount.sudo().search_count(
                [('state', '=', 'connected')]) if GscAccount is not None else 0
            rec.kpi_gsc_sites = GscSite.sudo().search_count(
                [('active', '=', True)]) if GscSite is not None else 0
            if GscQuery is not None:
                rec.kpi_gsc_clicks_28d = sum(GscQuery.sudo().search(
                    [('date', '>=', d28)]).mapped('clicks'))
                rec.kpi_gsc_impressions_28d = sum(GscQuery.sudo().search(
                    [('date', '>=', d28)]).mapped('impressions'))
            else:
                rec.kpi_gsc_clicks_28d = 0
                rec.kpi_gsc_impressions_28d = 0
            last_site = GscSite.sudo().search(
                [('last_pull_date', '!=', False)],
                order='last_pull_date desc', limit=1
            ) if GscSite is not None else False
            rec.kpi_gsc_last_pull = last_site.last_pull_date if last_site else False

    # ------------------------------------------------------------------------
    # Settings round-trip
    # ------------------------------------------------------------------------

    def _compute_settings(self):
        ICP = self.env['ir.config_parameter'].sudo()
        for rec in self:
            for fname, (key, kind, default) in self._SETTING_MAP.items():
                raw = ICP.get_param(key)
                if kind == 'bool':
                    rec[fname] = (raw in _TRUE) if raw not in ('', None) else default
                elif kind == 'int':
                    try:
                        rec[fname] = int(raw) if raw not in ('', None) else default
                    except (TypeError, ValueError):
                        rec[fname] = default
                else:
                    rec[fname] = raw or default

    def _inverse_settings(self):
        ICP = self.env['ir.config_parameter'].sudo()
        for rec in self:
            for fname, (key, kind, _default) in self._SETTING_MAP.items():
                val = rec[fname]
                if kind == 'bool':
                    ICP.set_param(key, 'True' if val else 'False')
                elif kind == 'int':
                    ICP.set_param(key, str(int(val or 0)))
                else:
                    ICP.set_param(key, val or '')

    def _compute_ai_agent_name(self):
        """Display the configured AI agent's name (era_seo_ai), if any."""
        ICP = self.env['ir.config_parameter'].sudo()
        agent_id = ICP.get_param('era_seo.ai_agent_id')
        for rec in self:
            rec.setting_ai_agent_name = ''
            if agent_id and 'ai.agent' in self.env:
                try:
                    agent = self.env['ai.agent'].sudo().browse(int(agent_id))
                    if agent.exists():
                        rec.setting_ai_agent_name = agent.name or ''
                except (TypeError, ValueError):
                    pass

    def _compute_gsc_redirect_uri(self):
        base = (self.env['ir.config_parameter'].sudo()
                .get_param('web.base.url') or '').rstrip('/')
        uri = (base + '/era_gsc/oauth/callback') if base \
            else '/era_gsc/oauth/callback'
        for rec in self:
            rec.setting_gsc_redirect_uri = uri

    # ------------------------------------------------------------------------
    # Open helpers used by the menu and Refresh button
    # ------------------------------------------------------------------------

    @api.model
    def action_open_hub(self):
        hub = self.search([], limit=1)
        if not hub:
            hub = self.create({'name': 'ERA SEO Suite'})
        return {
            'type': 'ir.actions.act_window',
            'name': _('ERA SEO Suite'),
            'res_model': self._name,
            'res_id': hub.id,
            'view_mode': 'form',
            'view_id': self.env.ref(
                'era_seo_suite.view_era_seo_suite_hub_form').id,
            'target': 'current',
        }

    def action_refresh(self):
        """Force a re-compute of the KPI fields (cheap; they're non-stored)."""
        self.invalidate_recordset()
        return True

    def action_open_website_settings(self):
        """Jump to the standard Website → Configuration → Settings page."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Settings'),
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'website'},
        }

    def action_run_audit_now(self):
        """Create a fresh SEO + GEO audit run, execute it, open the result.

        Synchronous; for large sites this may take a few seconds. The run
        scans every published website.page and stores findings on
        ``era.seo.audit.run``.
        """
        self.ensure_one()
        run = self.env['era.seo.audit.run'].create({})
        run._run_audit()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Audit Run'),
            'res_model': 'era.seo.audit.run',
            'res_id': run.id,
            'view_mode': 'form',
            'target': 'current',
        }
