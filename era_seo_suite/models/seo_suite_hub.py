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
from odoo.exceptions import UserError

from ..utils import slugify

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
        'setting_llms_enabled':     ('era_seo_suite.llms_enabled',          'bool', True),
        'setting_llms_summary':     ('era_seo_suite.site_summary',          'char', ''),
        'setting_llms_max_items':   ('era_seo_suite.llms_max_items',        'int', 100),
        'setting_llms_include_blog':('era_seo_suite.llms_include_blog',     'bool', True),
        # ---------- GSC (era_gsc) ----------
        'setting_gsc_client_id':    ('era_seo_suite.client_id',             'char', ''),
        'setting_gsc_client_secret':('era_seo_suite.client_secret',         'char', ''),
        'setting_gsc_pull_window':  ('era_seo_suite.pull_window_days',      'int', 28),
        # ---------- Auto-publish (era_seo_suite) ----------
        'setting_article_generator_active': ('era_seo.article_generator_active', 'bool', False),
        'setting_trends_geo':               ('era_seo.trends_geo',               'char', 'US'),
        'setting_article_prompt_addendum':  ('era_seo.article_prompt_addendum',  'char', ''),
        'setting_article_lang':             ('era_seo.article_lang',             'char', ''),
        'setting_article_interval_days':    ('era_seo.article_interval_days',    'int',  3),
        # Image generation
        'setting_image_provider':           ('era_seo.image_provider',           'char', 'openai'),
        'setting_image_api_key':            ('era_seo.image_api_key',            'char', ''),
        'setting_image_model':              ('era_seo.image_model',              'char', 'gpt-image-1'),
        'setting_image_size':               ('era_seo.image_size',               'char', '1024x1024'),
    }

    # --- Organization
    setting_org_name = fields.Char(
        string='Organization Name', compute='_compute_settings', inverse='_inverse_settings',
        help='Your organization name. Used in Organization JSON-LD and Twitter site tags.')
    setting_legal_name = fields.Char(
        string='Legal Name', compute='_compute_settings', inverse='_inverse_settings',
        help='Legal entity name. Used in Organization JSON-LD when different from the brand.')
    setting_logo_url = fields.Char(
        string='Logo URL', compute='_compute_settings', inverse='_inverse_settings',
        help='Absolute URL to the organization logo image. Renders into Organization JSON-LD.')
    setting_og_image_url = fields.Char(
        string='Default OG Image URL', compute='_compute_settings', inverse='_inverse_settings',
        help='Absolute URL to the default Open Graph image, used when a page has no per-page OG image.')
    setting_twitter_handle = fields.Char(
        string='Twitter Handle', compute='_compute_settings', inverse='_inverse_settings',
        help='Must start with @, e.g. @era. Used in twitter:site meta + Organization JSON-LD.')
    setting_google_verify = fields.Char(
        string='Google Site Verification', compute='_compute_settings', inverse='_inverse_settings',
        help='Value of the "content" attribute from Google Search Console\'s HTML meta verification tag.')
    setting_bing_verify = fields.Char(
        string='Bing Site Verification', compute='_compute_settings', inverse='_inverse_settings',
        help='Value of the "content" attribute from Bing Webmaster Tools\' meta verification tag.')
    setting_schema_engine = fields.Boolean(
        string='Schema Engine Enabled', compute='_compute_settings', inverse='_inverse_settings',
        help='Master switch for the JSON-LD schema engine. When off, the engine emits nothing.')

    # --- Social
    setting_social_facebook = fields.Char(
        string='Facebook', compute='_compute_settings', inverse='_inverse_settings',
        help='Full URL to your Facebook page. Joins sameAs[] in Organization JSON-LD.')
    setting_social_twitter = fields.Char(
        string='Twitter / X', compute='_compute_settings', inverse='_inverse_settings',
        help='Full URL to your Twitter/X profile. Joins sameAs[] in Organization JSON-LD.')
    setting_social_linkedin = fields.Char(
        string='LinkedIn', compute='_compute_settings', inverse='_inverse_settings',
        help='Full URL to your LinkedIn company page. Joins sameAs[] in Organization JSON-LD.')
    setting_social_instagram = fields.Char(
        string='Instagram', compute='_compute_settings', inverse='_inverse_settings',
        help='Full URL to your Instagram profile. Joins sameAs[] in Organization JSON-LD.')
    setting_social_youtube = fields.Char(
        string='YouTube', compute='_compute_settings', inverse='_inverse_settings',
        help='Full URL to your YouTube channel. Joins sameAs[] in Organization JSON-LD.')

    # --- AI
    setting_ai_enabled = fields.Boolean(
        string='AI Auto-Fix Enabled', compute='_compute_settings', inverse='_inverse_settings',
        help='Master switch. When off, all AI buttons hide and no AI calls happen.')
    setting_ai_agent_name = fields.Char(
        string='AI Agent (current)', compute='_compute_ai_agent_name', readonly=True,
        help='Read-only display of the configured agent name. Kept for any view that '
             'still references it; the picker below is the writable surface.')
    setting_ai_agent_id = fields.Many2one(
        'ai.agent',
        string='AI Agent',
        compute='_compute_ai_agent_id',
        inverse='_inverse_ai_agent_id',
        help='Which AI agent answers Suggest/Apply requests. The agent carries the '
             'provider, model, and API key (configured in the AI app). Leave empty '
             'to fall back to the site\'s "Ask AI" agent.')

    # --- GEO
    setting_llms_enabled = fields.Boolean(
        string='Publish /llms.txt', compute='_compute_settings', inverse='_inverse_settings',
        help='Serve a Markdown site map at /llms.txt for AI answer engines (llmstxt.org standard).')
    setting_llms_summary = fields.Char(
        string='Site Summary (llms.txt)', compute='_compute_settings', inverse='_inverse_settings',
        help='One-line description used as the blockquote intro in /llms.txt. Falls back to the '
             'company name when empty.')
    setting_llms_max_items = fields.Integer(
        string='Max Items in /llms.txt', compute='_compute_settings', inverse='_inverse_settings',
        help='How many pages (and blog posts) to include in /llms.txt.')
    setting_llms_include_blog = fields.Boolean(
        string='Include Blog in /llms.txt', compute='_compute_settings', inverse='_inverse_settings',
        help='Include published blog posts in /llms.txt under a "Blog" section.')

    # --- GSC
    setting_gsc_client_id = fields.Char(
        string='GSC OAuth Client ID', compute='_compute_settings', inverse='_inverse_settings',
        help='OAuth 2.0 Client ID from Google Cloud Console → APIs & Services → Credentials.')
    setting_gsc_client_secret = fields.Char(
        string='GSC OAuth Client Secret', compute='_compute_settings', inverse='_inverse_settings',
        help='OAuth 2.0 Client Secret matching the Client ID above.')
    setting_gsc_pull_window = fields.Integer(
        string='GSC Pull Window (days)', compute='_compute_settings', inverse='_inverse_settings',
        help='How many days of search analytics each Pull fetches. GSC data is ~2 days delayed.')
    setting_gsc_redirect_uri = fields.Char(
        string='GSC Authorized Redirect URI',
        compute='_compute_gsc_redirect_uri', readonly=True,
        help='Add this exact URL to your OAuth client\'s "Authorized redirect URIs" in Google '
             'Cloud Console — including https and no trailing slash.')

    # --- Recent AI-generated articles (read-only listing for the hub tab)
    recent_ai_article_ids = fields.Many2many(
        'blog.post',
        compute='_compute_recent_ai_articles',
        string='Recent AI-generated articles')

    # True while a background article-generation run is in flight. The Blog
    # Gen tab polls this and reloads the record every 3 seconds until the
    # cron clears the flag.
    is_article_pending = fields.Boolean(
        compute='_compute_is_article_pending', string='Article pending')

    def _compute_is_article_pending(self):
        ICP = self.env['ir.config_parameter'].sudo()
        pending = ICP.get_param('era_seo.article_pending') in _TRUE
        for rec in self:
            rec.is_article_pending = pending

    # Maximum age of a "pending" flag before the RPC treats it as stale
    # and clears it. The cron only takes 30-60s on a healthy run; 10 min
    # is generous enough to ride out an Odoo restart mid-generation
    # without the user being stuck on a spinner forever.
    _ARTICLE_PENDING_TTL_SECONDS = 10 * 60

    @api.model
    def get_article_pending_state(self):
        """Lightweight RPC for the OWL polling widget.

        Returns a plain dict the JS can read on each tick. Reads ICP
        directly to sidestep field-read caching.

        Self-healing: if pending has been True for more than
        ``_ARTICLE_PENDING_TTL_SECONDS`` without the cron clearing it
        (Odoo killed mid-generation, watchdog restart mid-flight, etc.)
        we clear the flag here so the spinner doesn't get stuck forever.

        Concurrency: many browser tabs may poll this RPC simultaneously
        while the cron is also writing the same ICP row. Postgres uses
        SERIALIZABLE-ish semantics on these rows, so naive `set_param`
        races throw `SerializationFailure` and roll back the WHOLE
        transaction — which, when the loser happens to be the cron, kills
        an otherwise-successful article generation. We wrap the only two
        writes here in their own savepoints so a concurrent update is
        absorbed silently. The other writer wins; we report whatever the
        winner left behind.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        pending = ICP.get_param('era_seo.article_pending') in _TRUE
        if pending:
            stamp_raw = ICP.get_param('era_seo.article_pending_started_at') or ''
            stamp = fields.Datetime.from_string(stamp_raw) if stamp_raw else None
            if stamp is None:
                # Legacy/unstamped pending — backfill so future ticks have
                # a clock, and report still-pending for now. Savepoint so a
                # race with the cron's flush doesn't poison the request.
                try:
                    with self.env.cr.savepoint():
                        ICP.set_param(
                            'era_seo.article_pending_started_at',
                            fields.Datetime.to_string(fields.Datetime.now()),
                        )
                except Exception:  # noqa: BLE001
                    pass
            else:
                age = (fields.Datetime.now() - stamp).total_seconds()
                if age > self._ARTICLE_PENDING_TTL_SECONDS:
                    _logger.warning(
                        'article_pending stuck True for %ds (> %ds TTL); '
                        'clearing — the cron either never ran or died '
                        'mid-generation. The user can re-click Generate Now.',
                        int(age), self._ARTICLE_PENDING_TTL_SECONDS)
                    try:
                        with self.env.cr.savepoint():
                            ICP.set_param('era_seo.article_pending', 'False')
                            ICP.set_param(
                                'era_seo.article_pending_started_at', '')
                    except Exception:  # noqa: BLE001
                        # A concurrent writer (the cron's `finally` clause)
                        # already cleared the flag — same end state.
                        pass
                    pending = False
        return {'pending': pending}

    def _compute_recent_ai_articles(self):
        Post = self.env.get('blog.post')
        if Post is None or 'era_ai_generated_at' not in Post._fields:
            for rec in self:
                rec.recent_ai_article_ids = [(5, 0, 0)]
            return
        recent = Post.sudo().search(
            [('era_ai_generated_at', '!=', False)],
            order='era_ai_generated_at desc', limit=20)
        for rec in self:
            rec.recent_ai_article_ids = [(6, 0, recent.ids)]

    # --- Auto-publish
    setting_article_generator_active = fields.Boolean(
        string='Auto-publish trend-aware article every 3 days',
        compute='_compute_settings', inverse='_inverse_settings',
        help='When on, the "ERA SEO: Auto-publish blog article" cron picks a '
             'current trend in your domain, writes a full article, and '
             'publishes it under the default blog. A notification email goes '
             'to the SEO Manager group with the post link.')
    setting_trends_geo = fields.Char(
        string='Google Trends geo (ISO-3166 alpha-2)',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Country code for the Google Trends daily-trends query the '
             'auto-publisher uses to seed topic selection (e.g. US, SA, GB, '
             'EG, AE). Defaults to US.')
    setting_article_lang = fields.Char(
        string='Article language code',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Target language for generated articles (e.g. en_US, ar_001). '
             'When empty, the agent auto-detects from your business_summary.')
    setting_article_prompt_addendum = fields.Text(
        string='Custom prompt guidance',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Free-form text appended to every article-generation prompt under '
             'an "ADMIN GUIDANCE" section. Use it to nudge tone of voice, '
             'preferred topics, audiences, forbidden subjects, regional '
             'angles, etc. Leave blank to use the suite\'s defaults.')
    setting_article_interval_days = fields.Integer(
        string='Frequency (days)',
        compute='_compute_settings', inverse='_inverse_settings',
        help='How often the auto-publisher cron runs. Saving this value '
             'updates the cron entry — the change takes effect at the '
             'NEXT scheduled tick.')
    # --- Image generation
    setting_image_provider = fields.Selection(
        [('none', 'None (skip image)'),
         ('openai', 'OpenAI (DALL-E)')],
        string='Image provider',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Service used to generate the article hero image. "None" '
             'leaves the post without a cover.')
    setting_image_api_key = fields.Char(
        string='Image API key',
        compute='_compute_settings', inverse='_inverse_settings',
        help='API key for the image provider. For OpenAI, leave blank to '
             'reuse the key configured on the active AI agent (when it '
             'happens to be an OpenAI agent).')
    setting_image_model = fields.Char(
        string='Image model',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Model identifier, e.g. dall-e-3, dall-e-2, gpt-image-1.')
    setting_image_size = fields.Char(
        string='Image size',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Provider-native size string (DALL-E: 1024x1024, 1792x1024, 1024x1792).')

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
                self.env, 'era_seo_suite.llms_enabled', True)

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
        # Intentionally a no-op. The shared inverse used to iterate
        # `_SETTING_MAP` and write every key to ICP, but reading other fields
        # mid-iteration triggers `_compute_settings`, which clobbers the
        # value the user just toggled — and other in-cache values that
        # happened to be False/empty at compute time end up persisted as
        # such. `write()` below routes each modified setting straight to
        # `ir.config_parameter` instead, so only the user's actual change
        # is persisted.
        return

    def write(self, vals):
        """Persist `_SETTING_MAP` fields directly to ir.config_parameter
        and strip them from `vals` so the framework never calls the shared
        `_inverse_settings`. See `_inverse_settings` for the why.

        Only the fields the caller actually passed are touched; other
        settings keep their existing ICP values intact.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        setting_vals = {}
        for fname in list(vals):
            if fname in self._SETTING_MAP:
                setting_vals[fname] = vals.pop(fname)
        if setting_vals:
            for fname, val in setting_vals.items():
                key, kind, _default = self._SETTING_MAP[fname]
                if kind == 'bool':
                    ICP.set_param(key, 'True' if val else 'False')
                elif kind == 'int':
                    ICP.set_param(key, str(int(val or 0)))
                else:
                    ICP.set_param(key, val or '')
            # Side effect: when the article-generator interval changes,
            # push it onto the cron entry so the scheduler picks it up at
            # the next tick.
            if 'setting_article_interval_days' in setting_vals:
                self._sync_article_cron_interval(
                    int(setting_vals['setting_article_interval_days'] or 3))
            # Drop the now-stale cache so the next read goes through
            # `_compute_settings` and reflects the values just written.
            self.invalidate_recordset(list(setting_vals))
        return super().write(vals) if vals else True

    @api.model
    def _sync_article_cron_interval(self, days):
        days = max(1, int(days or 1))
        cron = self.env.ref(
            'era_seo_suite.cron_generate_blog_article',
            raise_if_not_found=False)
        if cron and (cron.interval_number != days or cron.interval_type != 'days'):
            cron.sudo().write({
                'interval_number': days,
                'interval_type': 'days',
            })

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

    def _compute_ai_agent_id(self):
        """Resolve the configured AI agent id (era_seo.ai_agent_id ICP) into a recordset."""
        ICP = self.env['ir.config_parameter'].sudo()
        raw = ICP.get_param('era_seo.ai_agent_id')
        Agent = self.env['ai.agent']
        for rec in self:
            rec.setting_ai_agent_id = False
            if not raw:
                continue
            try:
                agent = Agent.sudo().browse(int(raw))
            except (TypeError, ValueError):
                continue
            # browse() doesn't hit the DB; .exists() filters out stale ids
            # (e.g. the agent was deleted after the setting was saved).
            if agent.exists():
                rec.setting_ai_agent_id = agent.id

    def _inverse_ai_agent_id(self):
        """Persist the picked agent back to era_seo.ai_agent_id ICP."""
        ICP = self.env['ir.config_parameter'].sudo()
        for rec in self:
            ICP.set_param('era_seo.ai_agent_id',
                          str(rec.setting_ai_agent_id.id) if rec.setting_ai_agent_id else '')

    def _compute_gsc_redirect_uri(self):
        base = (self.env['ir.config_parameter'].sudo()
                .get_param('web.base.url') or '').rstrip('/')
        uri = (base + '/era_gsc/oauth/callback') if base \
            else '/era_gsc/oauth/callback'
        for rec in self:
            rec.setting_gsc_redirect_uri = uri

    # ------------------------------------------------------------------------
    # Bulk AI fill — onboarding step + standalone cron
    # ------------------------------------------------------------------------
    #
    # When the onboarding wizard's "AI Bulk Fill" checkbox is ticked, the
    # wizard flips ``era_seo.bulk_ai_fill_active`` to True. A cron entry runs
    # every couple of minutes, checks the flag, and walks website.page and
    # blog.post records in batches — calling action_ai_fill_seo on each one
    # whose seo fields are still empty. This keeps the request that closes
    # the wizard fast (no synchronous AI calls) and stays robust under
    # restart / cancel.
    #
    # State is kept entirely in ir.config_parameter (no schema change):
    #   era_seo.bulk_ai_fill_active        bool   — cron checks this first
    #   era_seo.bulk_ai_fill_batch_size    int    — how many per cron tick
    #   era_seo.bulk_ai_fill_last_id__<m>  int    — high-water id per model

    _BULK_AI_MODELS = ('website.page', 'blog.post')

    # Hard wallclock budget for one bulk-fill tick. Odoo's default
    # `limit_time_cpu` is 600s; we leave ~60s headroom so a cleanly-
    # committed tick has time to write its cursors. The cron fires every
    # 2 minutes anyway, so anything we don't finish this tick gets picked
    # up on the next one.
    _BULK_AI_TICK_BUDGET_S = 540

    @api.model
    def cron_bulk_ai_fill(self):
        """Cron entry point — process one batch of pending records.

        Cron stays scheduled but is a no-op until
        ``era_seo.bulk_ai_fill_active`` is True. The cron record itself can
        be active=True permanently; the gate is the ICP flag.

        Each tick is bounded by ``_BULK_AI_TICK_BUDGET_S``: when the
        running time exceeds it we bail out of the inner loop, commit the
        per-record cursors written so far, and let the next cron tick
        pick up where we left off. This prevents one slow AI provider
        (or one big page) from running the whole tick into Odoo's
        ``limit_time_cpu`` deadline and rolling back the entire batch.
        """
        import time as _time
        deadline = _time.monotonic() + self._BULK_AI_TICK_BUDGET_S
        ICP = self.env['ir.config_parameter'].sudo()
        if _icp_get(self.env, 'era_seo.bulk_ai_fill_active') not in _TRUE:
            return
        try:
            batch_size = int(ICP.get_param('era_seo.bulk_ai_fill_batch_size', '10'))
        except (TypeError, ValueError):
            batch_size = 10
        if batch_size <= 0:
            batch_size = 10

        total_processed = 0
        for model_name in self._BULK_AI_MODELS:
            Model = self.env.get(model_name)
            if Model is None:
                continue
            key_last = 'era_seo.bulk_ai_fill_last_id__' + model_name
            try:
                last_id = int(ICP.get_param(key_last, '0'))
            except (TypeError, ValueError):
                last_id = 0
            # Records past the high-water mark that still look "needs SEO".
            # We hit only records that have an era.seo.mixin extension (so
            # action_ai_fill_seo exists). Filter on missing seo_title /
            # seo_description so we don't burn tokens on records the user
            # already filled by hand.
            domain = [
                ('id', '>', last_id),
                '|', ('seo_title', '=', False), ('seo_title', '=', ''),
            ]
            try:
                records = Model.sudo().search(domain, limit=batch_size, order='id')
            except Exception:  # noqa: BLE001
                _logger.exception(
                    'bulk_ai_fill: search failed on %s — skipping the model',
                    model_name)
                continue

            if not records:
                # All caught up for this model — leave the cursor where it
                # is so newly-created records get picked up on the next pass.
                continue

            for rec in records:
                if _time.monotonic() > deadline:
                    _logger.info(
                        'bulk_ai_fill: tick budget exhausted (%ds) — '
                        'stopping early; the next tick will resume from id %s',
                        self._BULK_AI_TICK_BUDGET_S, last_id)
                    break
                try:
                    # `_era_ai_system=True` bypasses the per-user SEO Manager
                    # group check in `_ai_check_manager`. The admin opted into
                    # the unattended bulk run by flipping the ICP flag, so the
                    # cron user (often the technical user, not a SEO Manager)
                    # is allowed to run the fill.
                    rec.with_context(_era_ai_system=True).action_ai_fill_seo()
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        'bulk_ai_fill: %s#%s failed — keeping the cursor moving',
                        model_name, rec.id)
                # Blog posts also get the optional taxonomy classification
                # in the same tick, so the user only needs one flag flipped.
                if model_name == 'blog.post' and \
                        _icp_get(self.env, 'era_seo.blog_taxonomy_active') in _TRUE:
                    try:
                        self._apply_blog_taxonomy(rec)
                    except Exception:  # noqa: BLE001
                        _logger.exception(
                            'bulk_ai_fill: taxonomy failed for blog.post#%s', rec.id)
                # Advance the cursor whether or not this record succeeded,
                # so one persistently-broken record can't stall the queue.
                ICP.set_param(key_last, str(rec.id))
                last_id = rec.id
                total_processed += 1
            else:
                # for-else: we didn't break out, so we may have more work.
                # Fall through to the next model.
                continue
            # We broke out of the per-record loop due to the deadline —
            # don't continue into other models this tick.
            break

        # If nothing was processed across any model, the queue is drained.
        # Flip the flag off so we don't keep waking the cron for no work.
        if total_processed == 0:
            ICP.set_param('era_seo.bulk_ai_fill_active', 'False')
            _logger.info('bulk_ai_fill: queue drained, flag cleared')
        else:
            _logger.info('bulk_ai_fill: processed %d record(s) this tick',
                         total_processed)

    def _apply_blog_taxonomy(self, post):
        """For one blog.post, pick a category + (optional) series via the AI
        agent and attach them. Idempotent: skips posts that already have a
        category set; reuses category/series by exact name (case-insensitive)
        before creating a new one."""
        # If the post already has a category, don't override it.
        if getattr(post, 'era_category_id', False) and post.era_category_id:
            return
        # ai_client lives outside this module's models so we import lazily.
        from .ai_client import AIClient
        client = AIClient(self.env)
        ok, _reason = client.is_available()
        if not ok:
            return
        pick = client.pick_blog_taxonomy(post)
        Category = self.env['era.blog.category'].sudo()
        Series = self.env['era.blog.series'].sudo()
        cat_name = pick['category'].strip()
        cat = Category.search([('name', '=ilike', cat_name)], limit=1)
        if not cat:
            cat = Category.create({'name': cat_name, 'slug': slugify(cat_name)})
        vals = {'era_category_id': cat.id}
        if pick['series']:
            ser_name = pick['series'].strip()
            ser = Series.search([('name', '=ilike', ser_name)], limit=1)
            if not ser:
                ser = Series.create({'name': ser_name, 'slug': slugify(ser_name)})
            vals['era_series_id'] = ser.id
        post.write(vals)

    @api.model
    def start_bulk_ai_fill(self):
        """Turn on the bulk AI fill flag and reset the per-model cursors so
        the next cron tick picks up from the beginning."""
        ICP = self.env['ir.config_parameter'].sudo()
        for model_name in self._BULK_AI_MODELS:
            ICP.set_param('era_seo.bulk_ai_fill_last_id__' + model_name, '0')
        ICP.set_param('era_seo.bulk_ai_fill_active', 'True')

    def action_generate_blog_article_now(self):
        """One-shot manual trigger for the auto-publish pipeline.

        Fire-and-forget: flips the pending flag, schedules the cron to run
        immediately, and returns. The Blog Gen tab polls `is_article_pending`
        every 3 seconds and reloads when the cron clears it.
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        # Flag + manual override of the gate, both consumed by the cron at
        # the start of its run. `_manual` is cleared inside the cron.
        # The timestamp drives get_article_pending_state()'s TTL — if the
        # cron never runs (e.g. Odoo restarted mid-trigger by the watchdog),
        # the spinner clears itself after _ARTICLE_PENDING_TTL_SECONDS.
        ICP.set_param('era_seo.article_pending', 'True')
        ICP.set_param(
            'era_seo.article_pending_started_at',
            fields.Datetime.to_string(fields.Datetime.now()),
        )
        ICP.set_param('era_seo.article_generator_manual', 'True')
        # Schedule the cron to run as soon as possible. `_trigger` is the
        # Odoo 17+ API for "fire this cron now".
        cron = self.env.ref(
            'era_seo_suite.cron_generate_blog_article',
            raise_if_not_found=False)
        if cron:
            try:
                cron.sudo()._trigger()
            except Exception:  # noqa: BLE001
                _logger.exception('Generate Now: cron _trigger failed')
        self.invalidate_recordset(['is_article_pending'])
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'info',
                'message': _('Generating article in the background. The tab '
                             'refreshes every few seconds — the new article '
                             'will appear in the table when it\'s ready.'),
                'sticky': False,
            },
        }

    @api.model
    def stop_bulk_ai_fill(self):
        """Cancel an in-progress bulk run. Cursors are kept where they are so
        the next start picks up rather than re-scans (callers that want a
        fresh start should use start_bulk_ai_fill which resets them)."""
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.bulk_ai_fill_active', 'False')

    # ------------------------------------------------------------------------
    # Weekly audit + auto AI-fix
    # ------------------------------------------------------------------------

    @api.model
    def cron_weekly_audit_and_fix(self):
        """Run a fresh SEO audit and let the AI agent fix every newly-found,
        AI-supported finding in one pass.

        Driven by a weekly ir.cron entry. The fix step uses
        ``action_ai_suggest_and_apply``, which only applies suggestions
        whose AI confidence is above the agent's threshold — so an
        unattended weekly run won't push low-confidence changes.

        Gated by `era_seo.weekly_audit_active` (default True): users can
        flip it off without disabling the cron entry.
        """
        if _icp_get(self.env, 'era_seo.weekly_audit_active', 'True') not in _TRUE:
            return
        Run = self.env['era.seo.audit.run'].sudo()
        try:
            run = Run.run_scheduled_audit()
        except Exception:  # noqa: BLE001
            _logger.exception('weekly_audit_and_fix: audit failed')
            return
        if not run:
            return
        # Only suggest+apply on findings the AI already knows how to fix
        # AND that haven't been touched yet in this run.
        findings = run.finding_ids.filtered(
            lambda f: f.ai_supported and f.ai_status == 'none')
        if not findings:
            _logger.info(
                'weekly_audit_and_fix: %s findings, none ai-supported / actionable',
                len(run.finding_ids))
            return
        try:
            findings.with_context(_era_ai_system=True).action_ai_suggest_and_apply()
        except Exception:  # noqa: BLE001
            _logger.exception(
                'weekly_audit_and_fix: AI suggest+apply failed on %d findings',
                len(findings))

    # ------------------------------------------------------------------------
    # Auto-publish: trend-aware blog article every N days
    # ------------------------------------------------------------------------

    @api.model
    def cron_generate_blog_article(self):
        """Wrapper that keeps the pending flag consistent across success,
        failure and re-entrancy. The actual work lives in `_run_article_gen`.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        # Mark pending + stamp the clock so the UI's TTL safety-net can
        # detect a run that dies without ever reaching the finally clause.
        ICP.set_param('era_seo.article_pending', 'True')
        ICP.set_param(
            'era_seo.article_pending_started_at',
            fields.Datetime.to_string(fields.Datetime.now()),
        )
        try:
            return self._run_article_gen()
        finally:
            # Always clear the pending flag — even when the run was a no-op
            # because the gate was off or the AI was unavailable. The UI's
            # spinner stops on the next poll tick.
            ICP.set_param('era_seo.article_pending', 'False')
            ICP.set_param('era_seo.article_pending_started_at', '')

    @api.model
    def _run_article_gen(self):
        """Generate a fresh, trend-aware blog post from the AI agent.

        Cron entry runs every 3 days. Gated by
        `era_seo.article_generator_active` (default False — opt-in).
        `era_seo.article_generator_manual` is a one-shot override flipped
        by `action_generate_blog_article_now` so admins can produce an
        article on demand even when the cron is paused.

        Pipeline:
          1. Read the site's business context (org name + /llms.txt summary)
             from ICP.
          2. Collect the last 30 article titles so the agent doesn't
             accidentally rehash one.
          3. Ask the AI agent for {title, content_html, seo meta, suggested
             category, image_prompt, trend_signal}.
          4. Find/create the suggested category.
          5. Create a blog.post with content + SEO meta.
          6. Generate a hero image via `_generate_article_image(prompt)`
             (default: returns no image — override or wire up a provider).

        Returns the created blog.post id, or False when nothing happened.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        # Manual override (set by Generate Now) consumed in one shot.
        manual = ICP.get_param('era_seo.article_generator_manual') in _TRUE
        if manual:
            ICP.set_param('era_seo.article_generator_manual', 'False')
        if not manual and _icp_get(self.env, 'era_seo.article_generator_active') not in _TRUE:
            return False
        BlogPost = self.env.get('blog.post')
        Category = self.env.get('era.blog.category')
        if BlogPost is None or Category is None:
            _logger.info(
                'cron_generate_blog_article: blog modules not installed — '
                'feature requires website_blog')
            return False
        ICP = self.env['ir.config_parameter'].sudo()
        from .ai_client import AIClient, AIUnavailable
        client = AIClient(self.env)
        ok, reason = client.is_available()
        if not ok:
            _logger.warning('cron_generate_blog_article: AI unavailable: %s', reason)
            return False

        business_context = {
            'org_name': ICP.get_param('era_seo.organization_name', ''),
            'summary':  ICP.get_param('era_seo_suite.site_summary', ''),
        }
        past_titles = BlogPost.sudo().search(
            [], order='id desc', limit=30).mapped('name')
        existing_categories = Category.sudo().search([], limit=50).mapped('name')
        # Real trends signal: today's top Google Trends for the configured geo.
        # Empty when offline / blocked / unparseable — the agent then falls
        # back to its own "what's relevant now" reasoning.
        trending_now = self._fetch_google_trends()
        lang_code = (ICP.get_param('era_seo.article_lang', '') or '').strip() or None
        prompt_addendum = (ICP.get_param('era_seo.article_prompt_addendum', '') or '').strip() or None
        try:
            article = client.propose_article(
                business_context, past_titles, existing_categories,
                lang_code=lang_code,
                trending_now=trending_now,
                prompt_addendum=prompt_addendum)
        except (AIUnavailable, ValueError) as exc:
            _logger.warning('cron_generate_blog_article: skipped — %s', exc)
            return False
        except UserError as exc:
            # Provider-config failure (no API key, quota exceeded, …). The
            # ai_client._resolve_agent path already WARNs naming the stale
            # ICP id; here we just need to record that this run was a no-op
            # without dumping a 30-line traceback that says nothing the
            # earlier warning didn't already say.
            _logger.warning(
                'cron_generate_blog_article: provider unavailable — %s '
                '(check Settings → ERA SEO → AI Auto-Fix)', exc)
            return False
        except Exception:  # noqa: BLE001
            _logger.exception('cron_generate_blog_article: AI call failed')
            return False

        # Find or create the category the agent picked.
        category = False
        if article['category']:
            cat = Category.sudo().search(
                [('name', '=ilike', article['category'])], limit=1)
            if not cat:
                # `slug` is NOT NULL on era.blog.category; the model's
                # _onchange_name only fires in the UI, so we slugify here.
                cat = Category.sudo().create({
                    'name': article['category'],
                    'slug': slugify(article['category']),
                })
            category = cat

        # Pick the default blog.blog — required FK on blog.post.
        Blog = self.env['blog.blog'].sudo()
        blog = Blog.search([], limit=1)
        if not blog:
            blog = Blog.create({'name': 'Blog'})

        post_vals = {
            'name': article['title'],
            'blog_id': blog.id,
            'content': article['content_html'],
            'is_published': True,  # Auto-publish per admin preference.
        }
        # Provenance fields — surfaced in the hub's Blog Generation tab so
        # admins can review AI output and the trends it followed.
        if 'era_ai_generated_at' in BlogPost._fields:
            post_vals['era_ai_generated_at'] = fields.Datetime.now()
        if 'era_ai_trend_signal' in BlogPost._fields:
            post_vals['era_ai_trend_signal'] = article.get('trend_signal') or ''
        if 'era_ai_confidence' in BlogPost._fields:
            post_vals['era_ai_confidence'] = float(article.get('confidence') or 0.0)
        # SEO meta — set only when the post model carries the field, since
        # the mixin's field set varies a touch across installations.
        seo_map = [
            ('seo_title',       article['seo_title']),
            ('seo_description', article['seo_description']),
            ('seo_keywords',    article['seo_keywords']),
        ]
        for fname, value in seo_map:
            if fname in BlogPost._fields and value:
                post_vals[fname] = value
        # Subtitle + category live on era_seo_blog's extension. Same guard.
        if 'era_subtitle' in BlogPost._fields and article['subtitle']:
            post_vals['era_subtitle'] = article['subtitle']
        if 'era_excerpt' in BlogPost._fields and article.get('excerpt'):
            post_vals['era_excerpt'] = article['excerpt']
        if 'era_category_id' in BlogPost._fields and category:
            post_vals['era_category_id'] = category.id

        post = BlogPost.sudo().create(post_vals)

        # Hero image — call the hook, attach to all relevant slots if it
        # returned bytes.
        try:
            image_bytes = self._generate_article_image(article['image_prompt'])
        except Exception:  # noqa: BLE001
            _logger.exception(
                'cron_generate_blog_article: image hook raised — skipping image')
            image_bytes = None
        if not image_bytes:
            provider = ICP.get_param('era_seo.image_provider', 'none') or 'none'
            _logger.info(
                'cron_generate_blog_article: no image generated '
                '(provider=%s) — post.cover stays empty. Configure the Blog '
                'Gen tab → Image generation to enable.', provider)
        else:
            try:
                self._attach_article_image(post, image_bytes, article)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    'cron_generate_blog_article: failed to attach generated image')

        # Notify the SEO Manager group with the live URL.
        try:
            self._notify_managers_about_new_article(post, article)
        except Exception:  # noqa: BLE001
            _logger.exception(
                'cron_generate_blog_article: notification email failed '
                '(post still created)')

        _logger.info(
            'cron_generate_blog_article: created blog.post#%d "%s" '
            '(trend: %s, confidence %.2f)',
            post.id, article['title'][:80], article['trend_signal'][:80],
            article['confidence'])
        return post.id

    def _attach_article_image(self, post, image_bytes, article):
        """Persist the generated image into every slot the blog + SEO stack
        renders from:

          * `seo_og_image` — Binary, on era.seo.mixin: drives the OG meta
            tag and Twitter card. Social shares (FB / WhatsApp / LinkedIn
            / Slack) will pick this up.
          * `era_cover_image` — Binary, on era_seo_blog's blog.post
            extension (when installed): the dedicated hero shown above
            the body on /blog/<post>.
          * `cover_properties` — stock blog.post JSON. Lets website_blog's
            cover header render the image even on installations without
            the era extension; we point it at the ir.attachment created
            below so the same URL is reused everywhere.
          * Inline `<img>` injected at the top of `content` so the image
            shows inside the post body too (RSS / AMP / paragraphs).
        """
        import base64
        import json as _json
        b64 = base64.b64encode(image_bytes)

        # 1. Stable URL via an ir.attachment.
        att_name = '%s.png' % (article.get('title') or 'article')[:80]
        Attachment = self.env['ir.attachment'].sudo()
        att = Attachment.create({
            'name': att_name,
            'datas': b64,
            'res_model': post._name,
            'res_id': post.id,
            'mimetype': 'image/png',
            'public': True,
        })
        image_url = '/web/image/%d' % att.id

        # 2. Direct binary slots — driven by `fields_get` so we silently
        #    skip fields that don't exist on this install.
        binary_targets = [n for n in ('seo_og_image', 'era_cover_image')
                          if n in post._fields]
        vals = {n: b64 for n in binary_targets}

        # 3. Inject the image at the top of the article body. We re-fetch
        #    the current content so this also works for the regenerate path.
        if 'content' in post._fields:
            current = post.content or ''
            if image_url not in (current or ''):
                vals['content'] = (
                    '<p><img src="%s" alt="%s" class="img-fluid"/></p>%s' % (
                        image_url,
                        (article.get('title') or '').replace('"', '&quot;'),
                        current,
                    )
                )

        # 4. Stock cover_properties — website_blog renders it on the
        #    post page header. Format covers both modern (image_src) and
        #    legacy (background-image) shapes.
        if 'cover_properties' in post._fields:
            vals['cover_properties'] = _json.dumps({
                'image_src': image_url,
                'background-image': "url('%s')" % image_url,
                'background_color_class': 'o_cc o_cc1',
                'opacity': '0.6',
                'resize_class': 'o_record_has_cover cover_full',
            })

        if vals:
            post.write(vals)

    def _notify_managers_about_new_article(self, post, article):
        """Send a one-shot notification email to every member of the SEO
        Manager group with the published article's URL + the AI's reasoning.
        """
        Mail = self.env['mail.mail'].sudo()
        group = self.env.ref('era_seo_suite.group_era_seo_manager',
                             raise_if_not_found=False)
        if not group:
            return
        # Odoo 19 renamed res.groups.users → user_ids (with all_user_ids
        # covering members inherited via implied groups). user_ids is what
        # we want here — just the directly-assigned SEO Managers.
        recipients = group.user_ids.filtered(lambda u: u.email)
        if not recipients:
            _logger.info(
                'cron_generate_blog_article: no SEO Manager has an email; '
                'skipping notification')
            return
        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url', '').rstrip('/')
        # blog.post exposes website_url via the website mixin.
        rel_url = getattr(post, 'website_url', None) or ''
        full_url = (base_url + rel_url) if (base_url and rel_url) else (rel_url or '#')
        body_html = self._build_article_notification_body(post, article, full_url)
        for user in recipients:
            Mail.create({
                'subject': _('New article auto-published: %s', article['title'][:120]),
                'body_html': body_html,
                'email_to': user.email,
                'email_from': self.env.user.email_formatted or
                              self.env['ir.mail_server']._get_default_from_address(),
            }).send()

    @staticmethod
    def _build_article_notification_body(post, article, full_url):
        # Plain inline HTML — no template, so this doesn't depend on
        # mail_template records being present after the upgrade.
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            'line-height:1.5;color:#333;">'
            '<p>The ERA SEO Suite just auto-published a new blog article.</p>'
            '<p><strong>Title:</strong> {title}<br/>'
            '<strong>Trend signal:</strong> {trend}<br/>'
            '<strong>AI confidence:</strong> {conf:.2f}</p>'
            '<p>'
            '<a href="{url}" style="display:inline-block;padding:8px 16px;'
            'background:#7c4cff;color:#fff;text-decoration:none;border-radius:4px;">'
            'Open the article</a>'
            '</p>'
            '<p style="color:#888;font-size:12px;">Reason for the pick: {reason}<br/>'
            'You can pause auto-publishing any time from the suite hub '
            '(Settings tab → Auto-publish toggle).</p>'
            '</div>'
        ).format(
            title=(article.get('title') or '').replace('<', '&lt;'),
            trend=(article.get('trend_signal') or '').replace('<', '&lt;'),
            reason=(article.get('reason') or '').replace('<', '&lt;'),
            conf=float(article.get('confidence') or 0.0),
            url=full_url,
        )

    def _generate_article_image(self, prompt):
        """Return raw image bytes (PNG/JPEG) for the article's hero, or
        None to skip.

        Dispatches on the `era_seo.image_provider` ICP setting:
          - 'none'   (default) → returns None
          - 'openai' → calls OpenAI's image-generation API
        Override this method in a custom addon for additional providers
        (Replicate / Stable Diffusion / etc.); return raw bytes or None.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        provider = (ICP.get_param('era_seo.image_provider', 'none') or 'none').strip().lower()
        if provider == 'openai':
            return self._generate_image_openai(prompt)
        return None

    def _generate_image_openai(self, prompt):
        """Call OpenAI's images endpoint and return the raw PNG bytes.

        Reads the API key from `era_seo.image_api_key`. Falls back to the
        key configured on the active ai.agent record when that key is
        blank AND the agent's provider is openai-compatible. Any failure
        (no key, network, non-2xx) is logged at WARNING and returns None
        so the cron continues.
        """
        import requests as _requests
        import base64 as _base64
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = (ICP.get_param('era_seo.image_api_key', '') or '').strip()
        if not api_key:
            api_key = self._reuse_ai_agent_openai_key()
        if not api_key:
            _logger.warning(
                'image-gen openai: no API key configured (Blog Gen tab '
                '→ Image API key); skipping')
            return None
        model = (ICP.get_param('era_seo.image_model', 'gpt-image-1') or 'gpt-image-1').strip()
        # Normalize the size string. Admins copy/paste from docs and end up
        # with the Unicode multiplication sign U+00D7 ('×') instead of the
        # ASCII 'x' OpenAI requires. Also fix the fullwidth variants for
        # completeness, then lowercase.
        size = (ICP.get_param('era_seo.image_size', '1024x1024') or '1024x1024').strip()
        for ch in ('×', '✕', '✖', 'ｘ', 'Ｘ'):
            size = size.replace(ch, 'x')
        size = size.replace('X', 'x')

        def _call(call_prompt, call_model, call_size):
            # `response_format` is rejected by gpt-image-1 and some
            # account tiers ("Unknown parameter: 'response_format'").
            # Omit it — the API picks its default (URL for dall-e-*,
            # b64_json for gpt-image-1) and we accept either below.
            body = {
                'model': call_model,
                'prompt': call_prompt[:4000],
                'n': 1,
                'size': call_size,
            }
            try:
                r = _requests.post(
                    'https://api.openai.com/v1/images/generations',
                    headers={
                        'Authorization': 'Bearer %s' % api_key,
                        'Content-Type': 'application/json',
                    },
                    # 10-minute timeout — the cron runs in the background,
                    # so it's fine to wait. DALL-E 3 and gpt-image-1 can
                    # both run several minutes at peak load.
                    json=body, timeout=600,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning('image-gen openai: network error: %s', exc)
                return None, None
            if r.status_code >= 400:
                detail = (r.text or '')[:500].replace('\n', ' ')
                return None, detail
            try:
                item = r.json()['data'][0]
            except Exception as exc:  # noqa: BLE001
                _logger.warning('image-gen openai: malformed 2xx response: %s', exc)
                return None, None
            # Both response shapes from /v1/images/generations.
            if item.get('b64_json'):
                try:
                    return _base64.b64decode(item['b64_json']), None
                except Exception as exc:  # noqa: BLE001
                    _logger.warning('image-gen openai: bad b64: %s', exc)
                    return None, None
            if item.get('url'):
                try:
                    # Same rationale as the POST above — background job,
                    # generous timeout for the PNG download.
                    dl = _requests.get(item['url'], timeout=600)
                    dl.raise_for_status()
                    return dl.content, None
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        'image-gen openai: image download failed: %s', exc)
                    return None, None
            return None, 'no b64_json or url in response'

        # Try the admin-configured model first; on "model does not exist"
        # walk a fallback chain of widely-available alternatives. Different
        # OpenAI tiers (project keys / org tiers / region) gate access to
        # different image models — the chain insulates the cron from that.
        model_chain = [model]
        for fb in ('gpt-image-1', 'dall-e-3', 'dall-e-2'):
            if fb not in model_chain:
                model_chain.append(fb)

        image_bytes = None
        last_detail = None
        for attempt_model in model_chain:
            image_bytes, err_detail = _call(prompt, attempt_model, size)
            if image_bytes:
                return image_bytes
            last_detail = err_detail
            _logger.warning(
                'image-gen openai: failed (model=%s size=%s) — %s',
                attempt_model, size, err_detail or '<no detail>')
            if not err_detail:
                break
            lower = err_detail.lower()
            # Move on to next model on "does not exist" / "model_not_found"
            # / "invalid model" — those won't get better with a different
            # prompt.
            if 'does not exist' in lower or 'model_not_found' in lower \
                    or 'invalid_value' in lower and 'model' in lower:
                continue
            # Content policy / size: one prompt-level retry on the SAME model.
            if 'content_policy' in lower or 'safety' in lower or 'must be one of' in lower:
                neutral_prompt = (
                    'A clean, brand-neutral editorial hero illustration '
                    'suitable as a blog cover image. Minimalist, modern, '
                    'photorealistic where reasonable. No text, no logos, '
                    'no recognisable people.'
                )
                _logger.info(
                    'image-gen openai: retrying with neutral prompt + 1024x1024')
                image_bytes, err2 = _call(neutral_prompt, attempt_model, '1024x1024')
                if image_bytes:
                    return image_bytes
                last_detail = err2 or last_detail
            # Other errors (auth, rate-limit, billing): bail — fallback
            # models won't change the outcome.
            break
        if last_detail:
            _logger.warning(
                'image-gen openai: all model attempts failed — last error: %s',
                last_detail)
        return None

    # ------------------------------------------------------------------------
    # SEO agent — per-site system prompt builder
    # ------------------------------------------------------------------------

    # The onboarding wizard's "Business Profile" step collects these values
    # and stores them under these ICP keys. Re-launching the wizard pre-fills
    # from the same keys, so the round-trip is symmetric.
    _BIZ_PROFILE_KEYS = {
        'name_en':       'era_seo.business_name_en',
        'name_ar':       'era_seo.business_name_ar',
        'sector':        'era_seo.business_sector',
        'audience':      'era_seo.business_audience',
        'region':        'era_seo.business_region',
        'voice':         'era_seo.business_voice',
        'keywords':      'era_seo.business_keywords',
        'avoid':         'era_seo.business_avoid',
    }

    # Sector library — drives sector-specific examples in the generated
    # prompt. The key is what the wizard's selection stores; the labels
    # are what the dropdown shows; the seed keywords + examples are what
    # gets inlined into the system_prompt when this sector is picked.
    _SECTOR_LIBRARY = {
        'services': {
            'label': 'Professional services / consulting',
            'examples': [
                ('missing_seo_title', 'Tax Consulting for Saudi SMEs — Riyadh',
                 'Names the service, audience and city; 42 chars.'),
                ('slug_contains_stopwords',
                 '/the-best-vat-services-in-saudi-arabia → /vat-services-saudi',
                 'Dropped the unprovable "best" and stop-words.'),
            ],
        },
        'ecommerce': {
            'label': 'E-commerce / online store',
            'examples': [
                ('missing_seo_title', 'Handmade Leather Bags — Free KSA Shipping',
                 'Product category + a concrete shipping promise.'),
                ('missing_meta_description',
                 'Shop our handmade leather collection — free shipping across '
                 'Saudi Arabia and 30-day returns on every order.',
                 'Verb, deliverable, trust signal, all under 160 chars.'),
            ],
        },
        'hospitality': {
            'label': 'Hospitality / restaurants / hotels',
            'examples': [
                ('missing_seo_title', 'Boutique Hotel in Old Jeddah — AlBalad Suites',
                 'Location and category up front, brand name last.'),
            ],
        },
        'healthcare': {
            'label': 'Healthcare / clinics',
            'examples': [
                ('missing_seo_title', 'Dental Implants Riyadh — Smile Clinic',
                 'Treatment, city, brand; 38 chars.'),
            ],
        },
        'realestate': {
            'label': 'Real estate / property',
            'examples': [
                ('missing_seo_title', 'Apartments for Sale in Al Olaya, Riyadh',
                 'Listing category + neighborhood + city.'),
            ],
        },
        'education': {
            'label': 'Education / training',
            'examples': [
                ('missing_seo_title', 'Online Arabic Courses — Live Classes from $19',
                 'Format, language, and an entry-level price hook.'),
            ],
        },
        'industrial': {
            'label': 'Industrial / manufacturing / B2B',
            'examples': [
                ('missing_seo_title', 'Industrial HVAC Supplier — Jubail Industrial City',
                 'Product class and the geographic specialty.'),
            ],
        },
        'tech': {
            'label': 'Software / SaaS / technology',
            'examples': [
                ('missing_seo_title', 'Cloud Accounting for Saudi SMEs | ZATCA-Ready',
                 'Primary keyword first, audience and compliance hook.'),
            ],
        },
        'other': {
            'label': 'Other / general',
            'examples': [],
        },
    }

    _VOICE_LIBRARY = {
        'formal': 'formal and authoritative — third person, no slang, no exclamation marks',
        'friendly': 'warm and conversational — second person ("you"), plain words, no jargon',
        'technical': 'precise and technical — exact terminology, numeric specifics, no marketing fluff',
        'persuasive': 'persuasive and benefit-led — verbs first, name the customer outcome, soft CTA',
    }

    @api.model
    def _build_seo_agent_prompt(self, profile=None):
        """Compose a per-site system prompt for the ERA SEO Fixer agent.

        :param profile: optional dict with the same keys as ``_BIZ_PROFILE_KEYS``
                        (``name_en``, ``name_ar``, ``sector``, ``audience``,
                        ``region``, ``voice``, ``keywords``, ``avoid``). When
                        omitted, values are read from ``ir.config_parameter``.
                        Passing an explicit profile lets the wizard render a
                        live preview without writing draft values to ICP.

        Returns the ready-to-write prompt text, or ``None`` when the profile
        is empty — in which case the caller should leave the agent's existing
        prompt alone (so the generic seed from ai_agent_data.xml keeps working).
        """
        if profile is None:
            ICP = self.env['ir.config_parameter'].sudo()
            profile = {k: (ICP.get_param(v) or '').strip()
                       for k, v in self._BIZ_PROFILE_KEYS.items()}
        else:
            # Normalise — caller might pass missing keys or non-strings.
            profile = {k: (profile.get(k) or '').strip()
                       for k in self._BIZ_PROFILE_KEYS}
        if not (profile['name_en'] or profile['name_ar']):
            return None

        sector = self._SECTOR_LIBRARY.get(profile['sector']) \
            or self._SECTOR_LIBRARY['other']
        voice = self._VOICE_LIBRARY.get(profile['voice'], self._VOICE_LIBRARY['friendly'])

        brand_en = profile['name_en'] or profile['name_ar']
        brand_ar = profile['name_ar'] or profile['name_en']
        brand_clause = (
            '"%s" (Arabic: "%s")' % (brand_en, brand_ar)
            if profile['name_ar'] and profile['name_en']
               and profile['name_ar'] != profile['name_en']
            else '"%s"' % brand_en
        )

        keywords = [k.strip() for k in profile['keywords'].split(',') if k.strip()]
        avoid = [k.strip() for k in profile['avoid'].split(',') if k.strip()]

        lines = [
            '# SEO Fixer for %s' % brand_en,
            '',
            'You are an SEO copywriter for %s, a %s%s. You fix one SEO defect '
            'on one web page at a time. The host application sends you the '
            'page\'s content and the specific defect; you reply with a single '
            'JSON object and nothing else.' % (
                brand_clause,
                sector['label'].lower(),
                (' targeting %s' % profile['audience']) if profile['audience'] else '',
            ),
            '',
            '## Brand voice',
            '',
            'Write in a tone that is %s.' % voice,
            '',
            '## Output contract (non-negotiable)',
            '',
            'Return exactly ONE JSON object, no markdown fences, no prose '
            'before or after:',
            '',
            '  {"proposed_value": "<string>", "explanation": "<one sentence>", '
            '"confidence": <number 0.0-1.0>}',
            '',
            'The host parses your reply with json.loads(). Any extra text breaks it.',
            '',
            '## Field rules',
            '',
            '### seo_title',
            '- 50-60 characters is the sweet spot. Hard cap at 60.',
            '- Lead with the primary keyword. Brand (%s%s) last, only if it fits.'
            % (
                '"%s"' % brand_en,
                ' / "%s"' % brand_ar if brand_ar and brand_ar != brand_en else '',
            ),
            '- Never ALL CAPS. Never keyword-stuff.',
            '',
            '### seo_description',
            '- 140-160 characters. Hard cap at 160. One complete sentence '
            'ending in a period.',
            '- Repeat the primary keyword once, near the start. Add a soft '
            'call to action.',
            '',
            '### URL slug',
            '- Lowercase only. Hyphens between words — no underscores, '
            'spaces, or special characters.',
            '- Drop stop-words (the, a, an, in, on, of, for, and; ال، في، '
            'من، إلى، على).',
            '- 3-5 meaningful words. Hard cap 75 characters. No leading or '
            'trailing hyphen.',
            '',
            '## Language',
            '',
            'Match the page\'s language. Arabic content gets Arabic copy; '
            'English content gets English copy. Mixed content — match the '
            'title\'s language and say so in your explanation.',
            '',
        ]

        if profile['region']:
            lines += [
                '## Market signals',
                '',
                'Pages clearly targeted at %s should surface high-value '
                'local keywords where they fit naturally. Don\'t force them '
                'onto pages that aren\'t about the local market.'
                % profile['region'],
                '',
            ]

        if keywords:
            lines += [
                '## Brand keywords to weave in (only when relevant)',
                '',
            ] + ['- %s' % k for k in keywords] + ['']

        if avoid:
            lines += [
                '## Words and claims to AVOID',
                '',
            ] + ['- %s' % k for k in avoid] + ['']

        lines += [
            '## Confidence scoring',
            '',
            '- 0.9-1.0: the page content makes the right answer obvious; '
            'one clear keyword.',
            '- 0.7-0.89: good signal, but you had to interpret intent or '
            'pick a keyword.',
            '- 0.4-0.69: thin or generic page; a reasonable guess the admin '
            'should review.',
            '- below 0.4: almost no signal; propose something safe and flag it.',
            '',
            '## Hard limits',
            '',
            '- Never invent facts about %s, products, prices, or features '
            'not in the page content.' % brand_en,
            '- Never exceed the character caps — truncate wording rather '
            'than overflow.',
            '- Never put URLs, phone numbers, or emails in titles or '
            'descriptions.',
            '- Never output a comma-separated keyword list.',
            '- Use the page H1 as INPUT, not as the verbatim output.',
            '',
        ]

        if sector['examples']:
            lines += ['## Worked examples (for this industry)', '']
            for defect, proposal, why in sector['examples']:
                lines += [
                    'DEFECT: %s' % defect,
                    'OUTPUT: %s' % proposal,
                    'WHY: %s' % why,
                    '',
                ]

        lines += [
            '# Final reminder',
            '',
            'Return JSON only. No markdown. No preamble. The host parses '
            'with json.loads().',
        ]
        return '\n'.join(lines)

    @api.model
    def rebuild_seo_agent_prompt(self):
        """Build the tailored prompt and write it to the configured SEO agent.

        Called by the onboarding wizard's Business Profile step. Safe to
        call from anywhere — no-op when the profile is empty or the agent
        is missing.
        """
        prompt = self._build_seo_agent_prompt()
        if not prompt:
            return False
        Agent = self.env.get('ai.agent')
        if Agent is None:
            return False
        # Prefer the agent the suite ships with; fall back to whichever
        # agent the user has selected.
        agent = self.env.ref('era_seo_suite.agent_seo',
                             raise_if_not_found=False)
        if not agent or not agent.exists():
            ICP = self.env['ir.config_parameter'].sudo()
            agent_id = ICP.get_param('era_seo.ai_agent_id')
            try:
                agent = Agent.sudo().browse(int(agent_id)) if agent_id else False
            except (TypeError, ValueError):
                agent = False
        if not agent or not agent.exists():
            return False
        agent.sudo().write({'system_prompt': prompt})
        _logger.info('SEO agent prompt rebuilt for agent #%s (%d chars)',
                     agent.id, len(prompt))
        return True

    def _reuse_ai_agent_openai_key(self):
        """Best-effort: locate an OpenAI API key already configured for the
        AI app, so admins don't have to paste the same key twice. Returns
        '' when none found.

        Odoo 19's AI app stores provider keys in ir.config_parameter
        under per-provider keys (the OpenAI one is `ai.openai_key`).
        Older / forks may keep them on the ai.agent record — checked as a
        secondary fallback.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        # Primary: the AI app's own config slot.
        key = (ICP.get_param('ai.openai_key') or '').strip()
        if key:
            return key
        # Fallback: the agent record itself, in case a fork stores it there.
        Agent = self.env.get('ai.agent')
        if Agent is None:
            return ''
        agent_id = ICP.get_param('era_seo.ai_agent_id')
        try:
            agent = Agent.sudo().browse(int(agent_id)) if agent_id else False
        except (TypeError, ValueError):
            agent = False
        if not agent or not agent.exists():
            return ''
        for fname in ('api_key', 'llm_api_key', 'provider_api_key'):
            if fname in agent._fields:
                val = (agent[fname] or '').strip()
                if val:
                    return val
        return ''

    # ------------------------------------------------------------------------
    # Google Trends — pull current daily trends for a geo
    # ------------------------------------------------------------------------

    # The endpoint Google's own Trends UI calls. Public, no API key,
    # returns JSON prefixed by `)]}',` (the standard Google JSONP-shield).
    _GOOGLE_TRENDS_URL = 'https://trends.google.com/trends/api/dailytrends'

    @api.model
    def _fetch_google_trends(self, geo=None, limit=12):
        """Return a list of currently-trending search queries for ``geo``.

        :param geo: ISO 3166-1 alpha-2 country code (e.g. 'US', 'SA', 'GB').
                    When None, reads `era_seo.trends_geo` ICP; falls back to 'US'.
        :param limit: cap on items returned.
        :returns: list of strings (may be empty on any failure — caller
                  should treat empty as "no signal, fall back to the AI's
                  own picking").

        Safe to call from a cron path: never raises, never logs at ERROR.
        Network/parse failures degrade silently to an empty list.
        """
        import json as _json
        import requests as _requests
        if not geo:
            geo = (self.env['ir.config_parameter'].sudo()
                   .get_param('era_seo.trends_geo', 'US') or 'US').upper().strip()
        params = {'hl': 'en-US', 'tz': '0', 'geo': geo, 'ns': '15'}
        try:
            resp = _requests.get(
                self._GOOGLE_TRENDS_URL,
                params=params,
                timeout=10,
                headers={'User-Agent': 'Mozilla/5.0 (era_seo_suite trends fetcher)'},
            )
            resp.raise_for_status()
        except _requests.HTTPError as exc:
            # 404 here means "no daily-trends list for this country" — common
            # for smaller geos and not actionable. Drop to INFO so it doesn't
            # spam the log; the agent falls back to its own trend reasoning.
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status == 404:
                _logger.info(
                    'Google Trends has no daily list for geo=%s — agent will '
                    'use its own trend reasoning instead', geo)
            else:
                _logger.warning(
                    'Google Trends fetch failed (geo=%s): %s', geo, exc)
            return []
        except Exception as exc:  # noqa: BLE001
            _logger.warning('Google Trends fetch failed (geo=%s): %s', geo, exc)
            return []
        text = resp.text or ''
        # Strip the XSSI guard prefix `)]}',\n` that Google emits.
        for prefix in (")]}',\n", ")]}',", ")]}'\n"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        try:
            data = _json.loads(text)
        except ValueError:
            _logger.warning(
                'Google Trends returned unparseable JSON (geo=%s)', geo)
            return []
        queries = []
        for day in (data.get('default', {}).get('trendingSearchesDays') or []):
            for item in (day.get('trendingSearches') or []):
                title = (item.get('title') or {}).get('query')
                if title:
                    queries.append(title)
                if len(queries) >= limit:
                    return queries
        return queries

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
