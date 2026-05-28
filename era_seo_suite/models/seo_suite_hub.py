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
    # Settings-tab toggles (round-trip through ir.config_parameter)
    # =========================================================================

    setting_ai_enabled = fields.Boolean(
        string='AI Auto-Fix Enabled',
        compute='_compute_settings', inverse='_inverse_setting_ai_enabled')
    setting_llms_enabled = fields.Boolean(
        string='Publish /llms.txt',
        compute='_compute_settings', inverse='_inverse_setting_llms_enabled')
    setting_llms_summary = fields.Char(
        string='Site Summary (llms.txt)',
        compute='_compute_settings', inverse='_inverse_setting_llms_summary')
    setting_gsc_pull_window = fields.Integer(
        string='GSC Pull Window (days)',
        compute='_compute_settings', inverse='_inverse_setting_gsc_pull_window')

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
        for rec in self:
            rec.setting_ai_enabled = _icp_bool(
                self.env, 'era_seo.ai_enabled', False)
            rec.setting_llms_enabled = _icp_bool(
                self.env, 'era_geo.llms_enabled', True)
            rec.setting_llms_summary = _icp_get(
                self.env, 'era_geo.site_summary', '')
            try:
                rec.setting_gsc_pull_window = int(_icp_get(
                    self.env, 'era_gsc.pull_window_days', '28') or 28)
            except (TypeError, ValueError):
                rec.setting_gsc_pull_window = 28

    def _inverse_setting_ai_enabled(self):
        for rec in self:
            _icp_set_bool(self.env, 'era_seo.ai_enabled', rec.setting_ai_enabled)

    def _inverse_setting_llms_enabled(self):
        for rec in self:
            _icp_set_bool(self.env, 'era_geo.llms_enabled', rec.setting_llms_enabled)

    def _inverse_setting_llms_summary(self):
        for rec in self:
            self.env['ir.config_parameter'].sudo().set_param(
                'era_geo.site_summary', rec.setting_llms_summary or '')

    def _inverse_setting_gsc_pull_window(self):
        for rec in self:
            self.env['ir.config_parameter'].sudo().set_param(
                'era_gsc.pull_window_days',
                str(int(rec.setting_gsc_pull_window or 28)))

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
