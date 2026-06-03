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
    kpi_audit_warning = fields.Integer(
        string='Warning', compute='_compute_kpis')
    kpi_audit_info = fields.Integer(
        string='Info', compute='_compute_kpis')
    kpi_findings_resolved_total = fields.Integer(
        string='Resolved findings (total)', compute='_compute_kpis')
    kpi_findings_resolved_7d = fields.Integer(
        string='Resolved last 7 days', compute='_compute_kpis')
    kpi_health_score = fields.Integer(
        string='SEO health score', compute='_compute_kpis')
    kpi_attention_items = fields.Integer(
        string='Items needing attention', compute='_compute_kpis')

    # Coverage — what fraction of published pages have the basics done.
    # Strings deliberately percentage-shaped (Integer 0-100) so the form
    # widget can render them as plain numbers without a custom widget.
    kpi_coverage_title_pct = fields.Integer(
        string='Pages with SEO title (%)', compute='_compute_kpis')
    kpi_coverage_meta_pct = fields.Integer(
        string='Pages with meta description (%)', compute='_compute_kpis')
    kpi_coverage_og_image_pct = fields.Integer(
        string='Pages with OG image (%)', compute='_compute_kpis')
    kpi_coverage_schema_pct = fields.Integer(
        string='Pages with a schema instance (%)', compute='_compute_kpis')

    # AI workflow signals — what the AI agent is doing for you.
    kpi_ai_findings_applied = fields.Integer(
        string='AI fixes applied', compute='_compute_kpis')
    kpi_ai_findings_suggested = fields.Integer(
        string='AI fixes awaiting review', compute='_compute_kpis')
    kpi_ai_findings_failed = fields.Integer(
        string='AI fixes failed', compute='_compute_kpis')

    # AI auto-fill progress — how far the unattended bulk-fill sweep has
    # gotten through the pages/posts that still need SEO fields. `done` are
    # records that now have a seo_title; `remaining` are what the cron has
    # left to process (its own search domain). `active` mirrors the ICP gate.
    kpi_ai_fill_total = fields.Integer(
        string='AI auto-fill — records in scope', compute='_compute_kpis')
    kpi_ai_fill_done = fields.Integer(
        string='AI auto-fill — filled', compute='_compute_kpis')
    kpi_ai_fill_remaining = fields.Integer(
        string='AI auto-fill — remaining', compute='_compute_kpis')
    kpi_ai_fill_pct = fields.Integer(
        string='AI auto-fill — % complete', compute='_compute_kpis')
    kpi_ai_fill_active = fields.Boolean(
        string='AI auto-fill running', compute='_compute_kpis')
    kpi_blog_taxonomy_remaining = fields.Integer(
        string='Blog posts awaiting categorization', compute='_compute_kpis')

    # Content — what's been written / generated.
    kpi_blog_posts_total = fields.Integer(
        string='Blog posts', compute='_compute_kpis')
    kpi_blog_posts_ai_generated = fields.Integer(
        string='AI-generated blog posts', compute='_compute_kpis')
    kpi_blog_posts_30d = fields.Integer(
        string='Blog posts (last 30 days)', compute='_compute_kpis')
    kpi_pages_missing_seo = fields.Integer(
        string='Published pages missing SEO title', compute='_compute_kpis')
    kpi_posts_missing_seo = fields.Integer(
        string='Blog posts missing SEO title', compute='_compute_kpis')

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
    kpi_gsc_ctr_28d = fields.Float(
        string='CTR (28d)', compute='_compute_kpis', digits=(16, 2))
    kpi_gsc_last_pull = fields.Date(
        string='GSC Last Pull', compute='_compute_kpis')
    kpi_gsc_queries_tracked = fields.Integer(
        string='Queries tracked (28d)', compute='_compute_kpis')
    kpi_gsc_avg_position_28d = fields.Float(
        string='Avg position (28d)', compute='_compute_kpis', digits=(16, 1))
    kpi_gsc_top_queries_html = fields.Html(
        string='Top GSC Queries (28d)', sanitize=False,
        compute='_compute_gsc_top_queries_html')

    # --- Analytics & Keywords tab (all rendered from era.gsc.query) ---
    analytics_trend_html = fields.Html(
        string='Search trend', sanitize=False, compute='_compute_analytics')
    analytics_summary_html = fields.Html(
        string='Analytics summary', sanitize=False, compute='_compute_analytics')

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
        # ---------- Smart 404 (did-you-mean redirect) ----------
        'setting_smart_404_enabled':       ('era_seo.smart_404_enabled',       'bool', True),
        'setting_smart_404_home_fallback': ('era_seo.smart_404_home_fallback', 'bool', True),
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
        'setting_image_model':              ('era_seo.image_model',              'char', 'gpt-image-2'),
        'setting_image_size':               ('era_seo.image_size',               'char', '1024x1024'),
        # Quality tier for OpenAI's gpt-image-1. 'low' is the cheap "mini"
        # tier (~$0.005/image), 'medium' is the default tier (~$0.04),
        # 'high' is the premium tier (~$0.17). Ignored by dall-e-* models
        # and by other providers.
        'setting_image_quality':            ('era_seo.image_quality',            'char', 'low'),
        # Optional OpenRouter key. Lets admins point Image generation at an
        # OpenRouter image-capable model (e.g. Google's nano-banana) instead
        # of OpenAI direct. Kept entirely optional — the default path stays
        # OpenAI + gpt-image-1.
        'setting_image_openrouter_key':     ('era_seo.image_openrouter_key',     'char', ''),
        # Blog taxonomy auto-classification — the bulk-fill cron reads this
        # and, when set, asks the AI to assign a category (and optionally
        # a series) to every blog.post in its sweep.
        'setting_blog_taxonomy_active':     ('era_seo.blog_taxonomy_active',     'bool', False),
        # ---------- Audit-run retention ----------
        'setting_run_retention_days':       ('era_seo.run_retention_days',       'int',  90),
        'setting_run_retention_active':     ('era_seo.run_retention_active',     'bool', True),
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

    # --- Smart 404 (did-you-mean redirect)
    setting_smart_404_enabled = fields.Boolean(
        string='Smart 404 redirects', compute='_compute_settings', inverse='_inverse_settings',
        help='On a "page not found", redirect to the closest matching existing URL '
             '(fuzzy match against the sitemap) instead of showing a 404.')
    setting_smart_404_home_fallback = fields.Boolean(
        string='Send unmatched 404s home', compute='_compute_settings', inverse='_inverse_settings',
        help='When no close match is found, log the 404 and redirect the visitor to '
             'the home page. Turn OFF to keep a real 404 for unmatched pages '
             '(recommended if SEO soft-404 signals are a concern).')

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

    # True while a background audit scan is in flight. The hub Dashboard
    # tab + the audit-run form poll this; the OWL widget triggers a
    # soft_reload as soon as the cron clears it, so the just-finished
    # run lands in the list / the form refreshes with the final counts.
    is_audit_pending = fields.Boolean(
        compute='_compute_is_audit_pending', string='Audit pending')

    def _compute_is_audit_pending(self):
        ICP = self.env['ir.config_parameter'].sudo()
        pending = ICP.get_param('era_seo.audit_pending') in _TRUE
        for rec in self:
            rec.is_audit_pending = pending

    # AI Bulk Fill state — surfaced on the Settings tab so admins can see
    # at a glance whether the cron is sweeping and how much is left.
    # Lifted from the onboarding wizard's step 9 (era.seo.onboarding.wizard).
    bulk_ai_already_running = fields.Boolean(
        compute='_compute_bulk_ai_state', string='Bulk fill running')
    bulk_ai_pending_count = fields.Integer(
        compute='_compute_bulk_ai_state',
        string='Pages / posts pending fill')

    def _compute_bulk_ai_state(self):
        ICP = self.env['ir.config_parameter'].sudo()
        running = ICP.get_param('era_seo.bulk_ai_fill_active') in _TRUE
        count = 0
        for model_name in self._BULK_AI_MODELS:
            Model = self.env.get(model_name)
            if Model is None:
                continue
            try:
                count += Model.sudo().search_count([
                    '|', ('seo_title', '=', False), ('seo_title', '=', ''),
                ])
            except Exception:  # noqa: BLE001
                # Model may exist without seo_title (extension not yet applied).
                continue
        for rec in self:
            rec.bulk_ai_already_running = running
            rec.bulk_ai_pending_count = count

    # Maximum age of a "pending" flag before the RPC treats it as stale
    # and clears it. A healthy generation takes 30-60s; anything past 90s
    # is almost certainly a stuck flag (Odoo restarted mid-run, cron
    # trigger never picked up, AI call hanging, etc.) rather than real
    # in-flight work. We'd rather show a stale spinner for 60s than for
    # 10 minutes — if the cron IS still working when we clear, it'll
    # republish its `True` on the next set_param.
    # Generation makes several multi-minute LLM calls (bounded to ~9 min by
    # _ARTICLE_GEN_BUDGET_S + image/create), so the stuck-spinner safety net
    # must be longer than a real run — 90s used to clear it mid-generation.
    _ARTICLE_PENDING_TTL_SECONDS = 1200

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
        while the cron is also writing the same ICP row. Odoo cursors run at
        REPEATABLE READ, so a concurrent UPDATE of the shared row throws
        `SerializationFailure` (40001) which aborts the WHOLE transaction — and
        a SAVEPOINT does NOT clear a 40001 (the earlier claim that it did was
        wrong). We therefore route the two self-heal writes here through
        `_era_commit_icp`, a short-lived independently-committed cursor that
        sidesteps the conflict instead of trying to recover from it. The other
        writer still wins; we report whatever the winner left behind.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        pending = ICP.get_param('era_seo.article_pending') in _TRUE
        if pending:
            stamp_raw = ICP.get_param('era_seo.article_pending_started_at') or ''
            stamp = fields.Datetime.from_string(stamp_raw) if stamp_raw else None
            if stamp is None:
                # Legacy/unstamped pending — backfill so future ticks have
                # a clock, and report still-pending for now. Side cursor so a
                # race with the cron's flush can't poison the request.
                self._era_commit_icp({
                    'era_seo.article_pending_started_at':
                        fields.Datetime.to_string(fields.Datetime.now()),
                })
            else:
                age = (fields.Datetime.now() - stamp).total_seconds()
                if age > self._ARTICLE_PENDING_TTL_SECONDS:
                    _logger.warning(
                        'article_pending stuck True for %ds (> %ds TTL); '
                        'clearing — the cron either never ran or died '
                        'mid-generation. The user can re-click Generate Now.',
                        int(age), self._ARTICLE_PENDING_TTL_SECONDS)
                    # Side cursor: a concurrent writer (the cron's `finally`
                    # clause) racing us here is fine — same end state.
                    self._era_commit_icp({
                        'era_seo.article_pending': 'False',
                        'era_seo.article_pending_started_at': '',
                    })
                    pending = False
        # The live step message (e.g. "Searching trends…", "Writing the
        # article…") the generator publishes on a side cursor as it works, so
        # the banner updates with each phase instead of a static line.
        message = ICP.get_param('era_seo.article_progress', '') if pending else ''
        return {'pending': pending, 'message': message}

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
        [('none',       'None (skip image)'),
         ('openai',     'OpenAI (gpt-image-2)'),
         ('openrouter', 'OpenRouter (any image-capable model)')],
        string='Image provider',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Service used to generate the article hero image. "None" '
             'leaves the post without a cover. OpenRouter lets you point '
             'this at any image-capable model on openrouter.ai.')
    setting_image_api_key = fields.Char(
        string='Image API key',
        compute='_compute_settings', inverse='_inverse_settings',
        help='API key for the image provider. For OpenAI, leave blank to '
             'reuse the key configured on the active AI agent (when it '
             'happens to be an OpenAI agent).')
    setting_image_openrouter_key = fields.Char(
        string='OpenRouter key',
        compute='_compute_settings', inverse='_inverse_settings',
        help='OPTIONAL. Used only when "Image provider" is set to '
             '"OpenRouter". Get a key at https://openrouter.ai/keys.')
    setting_image_model = fields.Char(
        string='Image model',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Model identifier. OpenAI: gpt-image-2 / gpt-image-1 / dall-e-3. '
             'OpenRouter: e.g. google/gemini-2.5-flash-image-preview, '
             'openai/gpt-image-2 or any image-capable model from '
             'openrouter.ai/models.')
    setting_image_size = fields.Char(
        string='Image size',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Provider-native size string (DALL-E: 1024x1024, 1792x1024, '
             '1024x1792). Ignored by some OpenRouter models.')
    setting_image_quality = fields.Selection(
        [('low',    'Low (~$0.005/img, "mini" tier)'),
         ('medium', 'Medium (~$0.04/img, default)'),
         ('high',   'High (~$0.17/img)'),
         ('auto',   'Auto (provider decides)')],
        string='Image quality',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Quality tier for OpenAI\'s gpt-image models. "Low" is the cheap '
             '"mini" tier (~$0.005/image, 8x cheaper than medium). '
             'Ignored by dall-e-* models and by OpenRouter.')

    # --- Blog taxonomy auto-classification
    setting_blog_taxonomy_active = fields.Boolean(
        string='Auto-classify blog posts (AI)',
        compute='_compute_settings', inverse='_inverse_settings',
        help='When on, the AI Bulk Fill cron also asks the AI to assign '
             'a category (and series, when the post is clearly part of '
             'a multi-part arc) to every blog.post it processes. Existing '
             'categories and series are reused before new ones are '
             'created.')
    blog_post_count = fields.Integer(
        compute='_compute_blog_counts', string='Blog posts')
    blog_uncategorized_count = fields.Integer(
        compute='_compute_blog_counts',
        string='Posts without category')
    blog_category_count = fields.Integer(
        compute='_compute_blog_counts', string='Categories')
    blog_series_count = fields.Integer(
        compute='_compute_blog_counts', string='Series')

    def _compute_blog_counts(self):
        BlogPost = self.env.get('blog.post')
        Category = self.env.get('era.blog.category')
        Series = self.env.get('era.blog.series')
        n_posts = (BlogPost.sudo().search_count([]) if BlogPost is not None else 0)
        n_uncategorized = (
            BlogPost.sudo().search_count([('era_category_id', '=', False)])
            if BlogPost is not None else 0
        )
        n_cats = Category.sudo().search_count([]) if Category is not None else 0
        n_series = Series.sudo().search_count([]) if Series is not None else 0
        for rec in self:
            rec.blog_post_count = n_posts
            rec.blog_uncategorized_count = n_uncategorized
            rec.blog_category_count = n_cats
            rec.blog_series_count = n_series

    # --- Audit-run retention
    setting_run_retention_days = fields.Integer(
        string='Run retention (days)',
        compute='_compute_settings', inverse='_inverse_settings',
        help='Audit-run rows older than this are deleted by the daily '
             'cleanup cron (and by the "Cleanup now" button). The findings '
             'they discovered are NOT deleted — they keep their AI '
             'suggestions and resolution status. Set to 0 to disable.')
    setting_run_retention_active = fields.Boolean(
        string='Auto-cleanup audit runs',
        compute='_compute_settings', inverse='_inverse_settings',
        help='When on, a daily cron prunes audit runs older than '
             '"Run retention (days)". Turn off if you want to manage '
             'cleanup manually via the button.')

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
        BlogPost = self.env.get('blog.post')

        from datetime import date, datetime, timedelta
        today = date.today()
        d7 = today - timedelta(days=7)
        d28 = today - timedelta(days=28)
        d30 = today - timedelta(days=30)
        # `datetime` for fields that store timestamps, not dates.
        d7_dt = datetime.combine(d7, datetime.min.time())
        d30_dt = datetime.combine(d30, datetime.min.time())

        # Coverage figures are per-published-page. Pre-search the set once
        # so the four percentages share one query rather than four.
        published_pages = Page.search([('website_published', '=', True)])
        n_published = len(published_pages)
        # Helper — returns int(percent) safely when n_published == 0.
        def _pct(numerator):
            if not n_published:
                return 0
            return int(round(100 * numerator / n_published))

        # Coverage. seo_title / seo_description are translatable JSONB
        # so an empty dict / missing key reads as falsy via the ORM. The
        # `bool(x)` filter is enough here.
        with_title = sum(1 for p in published_pages if p.seo_title) \
            if n_published else 0
        with_meta = sum(1 for p in published_pages if p.seo_description) \
            if n_published else 0
        with_og = sum(1 for p in published_pages if p.seo_og_image_url) \
            if n_published else 0
        if n_published and Instance is not None:
            # Schema instances are linked back to a page via res_model+res_id.
            inst_page_ids = set(Instance.sudo().search([
                ('active', '=', True),
                ('res_model', '=', 'website.page'),
            ]).mapped('res_id'))
            with_schema = sum(1 for p in published_pages
                              if p.id in inst_page_ids)
        else:
            with_schema = 0

        # AI auto-fill progress — mirror the bulk-fill cron's own scope so the
        # dashboard reads exactly what the unattended sweep will still touch.
        # Records in `_BULK_AI_MODELS` missing a seo_title ARE the cron queue.
        needs_fill_domain = ['|', ('seo_title', '=', False),
                             ('seo_title', '=', '')]
        ai_fill_total = 0
        ai_fill_remaining = 0
        for _model_name in self._BULK_AI_MODELS:
            _M = self.env.get(_model_name)
            if _M is None or 'seo_title' not in _M._fields:
                continue
            _M = _M.sudo()
            ai_fill_total += _M.search_count([])
            ai_fill_remaining += _M.search_count(needs_fill_domain)
        ai_fill_done = ai_fill_total - ai_fill_remaining
        ai_fill_pct = (int(round(100 * ai_fill_done / ai_fill_total))
                       if ai_fill_total else 0)
        ai_fill_active = _icp_bool(self.env, 'era_seo.bulk_ai_fill_active', False)
        # Blog posts still awaiting AI taxonomy (the cron skips ones already
        # categorized). Guard on the field in case the blog isn't installed.
        _BP = self.env.get('blog.post')
        if _BP is not None and 'era_category_id' in _BP._fields:
            blog_taxonomy_remaining = _BP.sudo().search_count(
                [('era_category_id', '=', False)])
        else:
            blog_taxonomy_remaining = 0

        for rec in self:
            rec.kpi_published_pages = n_published
            rec.kpi_active_redirects = Redirect.sudo().search_count(
                [('is_active', '=', True)]) if Redirect is not None else 0
            rec.kpi_schema_instances = Instance.sudo().search_count(
                [('active', '=', True)]) if Instance is not None else 0

            last_run = Run.sudo().search(
                [('state', '=', 'done')], order='date_finished desc', limit=1
            ) if Run is not None else False
            rec.kpi_audit_last_date = last_run.date_finished if last_run else False
            if Finding is not None:
                F = Finding.sudo()
                rec.kpi_audit_open_findings = F.search_count(
                    [('is_resolved', '=', False)])
                rec.kpi_audit_critical = F.search_count(
                    [('is_resolved', '=', False), ('severity', '=', 'critical')])
                rec.kpi_audit_warning = F.search_count(
                    [('is_resolved', '=', False), ('severity', '=', 'warning')])
                rec.kpi_audit_info = F.search_count(
                    [('is_resolved', '=', False), ('severity', '=', 'info')])
                rec.kpi_findings_resolved_total = F.search_count(
                    [('is_resolved', '=', True)])
                rec.kpi_findings_resolved_7d = F.search_count(
                    [('is_resolved', '=', True),
                     ('resolved_date', '>=', d7_dt)])
                # AI workflow signals — count each ai_status bucket among
                # findings that aren't already resolved (applied counts the
                # cumulative wins regardless of resolution status).
                rec.kpi_ai_findings_applied = F.search_count(
                    [('ai_status', '=', 'applied')])
                rec.kpi_ai_findings_suggested = F.search_count(
                    [('ai_status', '=', 'suggested'),
                     ('is_resolved', '=', False)])
                rec.kpi_ai_findings_failed = F.search_count(
                    [('ai_status', '=', 'failed'),
                     ('is_resolved', '=', False)])
            else:
                rec.kpi_audit_open_findings = 0
                rec.kpi_audit_critical = 0
                rec.kpi_audit_warning = 0
                rec.kpi_audit_info = 0
                rec.kpi_findings_resolved_total = 0
                rec.kpi_findings_resolved_7d = 0
                rec.kpi_ai_findings_applied = 0
                rec.kpi_ai_findings_suggested = 0
                rec.kpi_ai_findings_failed = 0

            rec.kpi_coverage_title_pct = _pct(with_title)
            rec.kpi_coverage_meta_pct = _pct(with_meta)
            rec.kpi_coverage_og_image_pct = _pct(with_og)
            rec.kpi_coverage_schema_pct = _pct(with_schema)
            rec.kpi_pages_missing_seo = max(0, n_published - with_title)

            if BlogPost is not None:
                BP = BlogPost.sudo()
                rec.kpi_blog_posts_total = BP.search_count([])
                # The blog autoposter stamps era_ai_generated_at on every
                # AI-created post. Module load order is permissive — if
                # the field isn't there we treat the count as 0.
                if 'era_ai_generated_at' in BP._fields:
                    rec.kpi_blog_posts_ai_generated = BP.search_count(
                        [('era_ai_generated_at', '!=', False)])
                else:
                    rec.kpi_blog_posts_ai_generated = 0
                rec.kpi_blog_posts_30d = BP.search_count(
                    [('create_date', '>=', d30_dt)])
                if 'seo_title' in BP._fields:
                    rec.kpi_posts_missing_seo = BP.search_count(needs_fill_domain)
                else:
                    rec.kpi_posts_missing_seo = 0
            else:
                rec.kpi_blog_posts_total = 0
                rec.kpi_blog_posts_ai_generated = 0
                rec.kpi_blog_posts_30d = 0
                rec.kpi_posts_missing_seo = 0

            rec.kpi_ai_fill_total = ai_fill_total
            rec.kpi_ai_fill_done = ai_fill_done
            rec.kpi_ai_fill_remaining = ai_fill_remaining
            rec.kpi_ai_fill_pct = ai_fill_pct
            rec.kpi_ai_fill_active = ai_fill_active
            rec.kpi_blog_taxonomy_remaining = blog_taxonomy_remaining

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
                recent = GscQuery.sudo().search([('date', '>=', d28)])
                rec.kpi_gsc_clicks_28d = sum(recent.mapped('clicks'))
                rec.kpi_gsc_impressions_28d = sum(recent.mapped('impressions'))
                rec.kpi_gsc_queries_tracked = len(set(recent.mapped('query')))
                positions = [p for p in recent.mapped('position') if p]
                rec.kpi_gsc_avg_position_28d = (
                    round(sum(positions) / len(positions), 1) if positions else 0.0)
            else:
                rec.kpi_gsc_clicks_28d = 0
                rec.kpi_gsc_impressions_28d = 0
                rec.kpi_gsc_queries_tracked = 0
                rec.kpi_gsc_avg_position_28d = 0.0
            rec.kpi_gsc_ctr_28d = (
                round(100 * rec.kpi_gsc_clicks_28d / rec.kpi_gsc_impressions_28d, 2)
                if rec.kpi_gsc_impressions_28d else 0.0
            )
            last_site = GscSite.sudo().search(
                [('last_pull_date', '!=', False)],
                order='last_pull_date desc', limit=1
            ) if GscSite is not None else False
            rec.kpi_gsc_last_pull = last_site.last_pull_date if last_site else False

            coverage_score = int(round((
                rec.kpi_coverage_title_pct +
                rec.kpi_coverage_meta_pct +
                rec.kpi_coverage_og_image_pct +
                rec.kpi_coverage_schema_pct
            ) / 4)) if n_published else 0
            rec.kpi_attention_items = (
                rec.kpi_audit_critical +
                rec.kpi_audit_warning +
                rec.kpi_ai_findings_failed +
                rec.kpi_geo_crawlers_blocked
            )
            penalty = min(
                45,
                rec.kpi_audit_critical * 10 +
                rec.kpi_audit_warning * 2 +
                rec.kpi_ai_findings_failed * 3 +
                rec.kpi_geo_crawlers_blocked
            )
            rec.kpi_health_score = max(0, min(100, coverage_score - penalty))

    def _compute_gsc_top_queries_html(self):
        """Render the top search queries of the last 28 days (aggregated per
        query) as a compact HTML table for the dashboard."""
        from markupsafe import Markup, escape
        from datetime import date, timedelta
        GscQuery = self.env.get('era.gsc.query')
        d28 = date.today() - timedelta(days=28)
        rows_html = ''
        if GscQuery is not None:
            groups = GscQuery.sudo()._read_group(
                [('date', '>=', d28)],
                groupby=['query'],
                aggregates=['clicks:sum', 'impressions:sum', 'position:avg'],
                order='clicks:sum desc',
                limit=10,
            )
            for query, clicks, impressions, position in groups:
                ctr = (100.0 * (clicks or 0) / impressions) if impressions else 0.0
                rows_html += (
                    '<tr>'
                    '<td class="text-truncate" style="max-width:280px">%s</td>'
                    '<td class="text-end fw-bold">%d</td>'
                    '<td class="text-end">%d</td>'
                    '<td class="text-end">%.1f%%</td>'
                    '<td class="text-end">%.1f</td>'
                    '</tr>' % (escape(query or '—'), clicks or 0,
                              impressions or 0, ctr, position or 0.0)
                )
        if not rows_html:
            html = ('<div class="text-muted small">No GSC query data yet — '
                    'connect a Google account and pull, then backfill 3 months.</div>')
        else:
            html = (
                '<table class="table table-sm table-hover mb-0">'
                '<thead><tr class="small text-muted">'
                '<th>Query</th><th class="text-end">Clicks</th>'
                '<th class="text-end">Impr.</th><th class="text-end">CTR</th>'
                '<th class="text-end">Pos.</th></tr></thead>'
                '<tbody>' + rows_html + '</tbody></table>'
            )
        for rec in self:
            rec.kpi_gsc_top_queries_html = Markup(html)

    # ------------------------------------------------------------------------
    # Analytics & Keywords tab — best-practice SEO insights from GSC
    # ------------------------------------------------------------------------

    # English + Arabic question prefixes -> "content idea" keywords.
    _QUESTION_PREFIXES = (
        'how', 'what', 'why', 'who', 'where', 'when', 'which', 'can', 'does',
        'do ', 'is ', 'are ', 'should', 'best ', 'top ',
        'كيف', 'ما', 'ماذا', 'لماذا', 'من', 'اين', 'أين', 'متى', 'هل', 'كم',
        'افضل', 'أفضل',
    )

    _ANALYTICS_FIELDS = (
        'analytics_trend_html', 'analytics_summary_html')

    def _compute_analytics(self):
        """Guarded entry point — a data/render edge case must never break the
        always-loaded hub form, so any failure degrades to a notice."""
        try:
            self._compute_analytics_impl()
        except Exception:  # noqa: BLE001
            _logger.exception('analytics compute failed')
            from markupsafe import Markup
            note = Markup('<div class="text-muted small p-2">Analytics '
                          'temporarily unavailable.</div>')
            for rec in self:
                for f in self._ANALYTICS_FIELDS:
                    rec[f] = note

    def _compute_analytics_impl(self):
        """Render every Analytics-tab block from the last ~30 days of
        era.gsc.query. One DB pass per block via _read_group; pure-Python
        post-filtering for the opportunity buckets (no fragile HAVING)."""
        from markupsafe import Markup
        from datetime import date, timedelta

        Q = self.env.get('era.gsc.query')
        today = date.today()
        d28 = today - timedelta(days=28)
        d30 = today - timedelta(days=30)
        empty = ('<div class="text-muted small p-2">No Search Console data yet — '
                 'connect a Google account, pull, then <b>Backfill 3 Months</b> '
                 '(GSC tab) to populate these insights.</div>')

        # ---- small render helpers -------------------------------------
        def _ctr(clicks, impr):
            return (100.0 * clicks / impr) if impr else 0.0

        def _trend_chart(per_day):
            """per_day: ordered list of (date, clicks, impr). CSS bar chart:
            light impression bars with solid click bars in front."""
            if not per_day:
                return Markup(empty)
            max_impr = max((p[2] or 0) for p in per_day) or 1
            bars = ''
            for d, clk, imp in per_day:
                ih = round(100.0 * (imp or 0) / max_impr)
                ch = round(100.0 * (clk or 0) / max_impr)
                # Two bars anchored to the bottom of a full-height wrapper, so
                # each height:%% resolves against the 140px track (a bar nested
                # inside the impression bar would collapse / mis-scale). Clicks
                # (solid) overlay impressions (light); clicks <= impressions so
                # the solid bar always sits in front.
                bars += (
                    '<div style="flex:1 1 0; position:relative; height:100%%; '
                    'min-width:3px" '
                    'title="%s — %d clicks, %d impressions (CTR %.1f%%)">'
                    '<div style="position:absolute; bottom:0; left:12%%; '
                    'right:12%%; height:%d%%; background:#cfe2ff; '
                    'border-radius:2px 2px 0 0"></div>'
                    '<div style="position:absolute; bottom:0; left:12%%; '
                    'right:12%%; height:%d%%; background:#0d6efd; '
                    'border-radius:2px 2px 0 0"></div></div>'
                    % (d, clk or 0, imp or 0, _ctr(clk, imp),
                       max(ih, 1) if imp else 0,
                       max(ch, 1) if clk else 0))
            return Markup(
                '<div class="d-flex align-items-end gap-1 px-1" '
                'style="height:140px; border-bottom:1px solid #dee2e6">'
                + bars + '</div>'
                '<div class="d-flex justify-content-between small text-muted mt-1 px-1">'
                '<span>%s</span><span>'
                '<span style="color:#0d6efd">■</span> clicks &#160; '
                '<span style="color:#cfe2ff">■</span> impressions</span>'
                '<span>%s</span></div>'
                % (per_day[0][0], per_day[-1][0]))

        # ---- no data / no model short-circuit -------------------------
        blanks = dict.fromkeys((
            'analytics_trend_html', 'analytics_summary_html'), Markup(empty))
        if Q is None:
            for rec in self:
                for f, v in blanks.items():
                    rec[f] = v
            return
        Q = Q.sudo()

        # ---- 1. trend (clicks/impr per day, 30d) ----------------------
        per_day = [(g[0].strftime('%m-%d'), g[1] or 0, g[2] or 0)
                   for g in Q._read_group(
                       [('date', '>=', d30)], ['date:day'],
                       ['clicks:sum', 'impressions:sum'], order='date:day')]
        trend_html = _trend_chart(per_day)

        # ---- aggregate per query over 28d (one pass, reused below) ----
        agg = Q._read_group(
            [('date', '>=', d28)], ['query'],
            ['clicks:sum', 'impressions:sum', 'position:avg'],
            order='impressions:sum desc', limit=2000)
        # agg rows: (query, clicks, impr, pos)
        tot_clicks = sum(r[1] or 0 for r in agg)
        tot_impr = sum(r[2] or 0 for r in agg)
        summary_html = Markup(
            '<div class="mb-2">'
            '<div class="fw-bolder small mb-1">Search summary (28 days)</div>'
            '<div class="card d-inline-block"><div class="card-body py-1 px-3">'
            '<div class="d-flex justify-content-between gap-4 border-bottom py-1">'
            '<span class="text-muted small"><i class="fa fa-key me-1"/>Keywords</span>'
            '<span class="fw-bold">%d</span></div>'
            '<div class="d-flex justify-content-between gap-4 border-bottom py-1">'
            '<span class="text-muted small"><i class="fa fa-mouse-pointer me-1"/>Clicks</span>'
            '<span class="fw-bold">%d</span></div>'
            '<div class="d-flex justify-content-between gap-4 border-bottom py-1">'
            '<span class="text-muted small"><i class="fa fa-eye me-1"/>Impressions</span>'
            '<span class="fw-bold">%d</span></div>'
            '<div class="d-flex justify-content-between gap-4 py-1">'
            '<span class="text-muted small"><i class="fa fa-percent me-1"/>Avg CTR</span>'
            '<span class="fw-bold">%.1f%%</span></div>'
            '</div></div></div>'
            % (len(agg), tot_clicks, tot_impr, _ctr(tot_clicks, tot_impr)))

        vals = {
            'analytics_trend_html': trend_html,
            'analytics_summary_html': summary_html,
        }
        for rec in self:
            for f, v in vals.items():
                rec[f] = v

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

    # Hard wallclock budget for one bulk-fill tick. NOTE: the relevant kill
    # switch is `limit_time_real_cron` (real/wallclock time — deployment sets
    # it to 1200s; core default resolves to `limit_time_real`), NOT
    # `limit_time_cpu` (default 60s): AI calls sleep on network I/O, which
    # burns wallclock but almost no CPU, so the CPU limit never fires on them.
    # We keep a conservative budget well under the 1200s real-time ceiling so
    # we never *start* a record we can't *finish*; whatever's left is picked
    # up by the next tick. (A per-AI-call wallclock cap belongs in the agent
    # layer and is tracked separately — this budget only bounds the loop.)
    _BULK_AI_TICK_BUDGET_S = 360

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
                    #
                    # Each record runs in its OWN savepoint so a DB error on one
                    # record (e.g. a transient serialization conflict) rolls back
                    # only that record and can't poison the cursor for the rest
                    # of the tick. The field writes stay on the main cron cursor
                    # (committed by the cron framework at end-of-tick) — we do
                    # NOT call cr.commit() here, since this method runs as a
                    # framework-managed `state=code` server action that owns the
                    # transaction boundary.
                    with self.env.cr.savepoint():
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
                        with self.env.cr.savepoint():
                            self._apply_blog_taxonomy(rec)
                    except Exception:  # noqa: BLE001
                        _logger.exception(
                            'bulk_ai_fill: taxonomy failed for blog.post#%s', rec.id)
                # Advance the high-water mark on a SEPARATE, immediately-committed
                # cursor. The old in-transaction `ICP.set_param` UPDATE on the
                # shared ir_config_parameter row was the 40001 source that used
                # to abort the entire tick (InFailedSqlTransaction cascade). The
                # side cursor sidesteps the conflict entirely. We advance whether
                # or not the record succeeded, so one broken record can't stall
                # the queue.
                self._era_commit_icp({key_last: str(rec.id)})
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
            # Side cursor again — same shared-row 40001 hazard as the high-water.
            self._era_commit_icp({'era_seo.bulk_ai_fill_active': 'False'})
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

    def action_cleanup_old_runs_now(self):
        """Hub-side wrapper for the Settings tab button.

        Just delegates to era.seo.audit.run.action_cleanup_old_runs() so the
        retention logic lives in one place. The model method already returns
        an `ir.actions.client` notification with the count.
        """
        self.ensure_one()
        return self.env['era.seo.audit.run'].sudo().action_cleanup_old_runs()

    def action_generate_blog_article_now(self):
        """One-shot manual trigger for the auto-publish pipeline.

        Fire-and-forget: flips the pending flag, schedules the cron to run
        immediately, and returns a soft_reload so the form re-reads
        is_article_pending immediately. Without the reload, the Generate
        button stayed visible until the polling widget's next tick (up to
        3 seconds), letting users spam-click.
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        reload_action = {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        cron = self.env.ref(
            'era_seo_suite.cron_generate_blog_article',
            raise_if_not_found=False)
        # Generation stopped? That cron is the only worker that BOTH runs the
        # job and clears the pending flag. With it deactivated, seeding the flag
        # here would spin the banner forever (nothing ever clears it). Tell the
        # user instead of leaving a stuck "Preparing…" spinner.
        if not cron or not cron.sudo().active:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'warning',
                    'title': _('Article generation is off'),
                    'message': _(
                        'Blog article generation is currently disabled. '
                        'Re-enable it before generating a new article.'),
                    'sticky': False,
                },
            }
        # Any click first CLEARS the queue so rapid clicks never accumulate.
        if cron:
            self.env['ir.cron.trigger'].sudo().search(
                [('cron_id', '=', cron.id)]).unlink()
        # If a run is ALREADY in flight, the cleared queue is enough — do NOT
        # enqueue another. Enqueuing one per click is what made the banner cycle
        # "Preparing… → Writing…" several times: each extra click ran a fresh
        # generation back-to-back. (Server-side guard, so a stale cached widget
        # that still shows the button mid-run can't stack runs either.)
        if ICP.get_param('era_seo.article_pending') in _TRUE:
            self.invalidate_recordset(['is_article_pending'])
            return reload_action
        # Idle → seed the flags + an initial step (so the banner has something
        # to show before the cron writes its first real step) and enqueue
        # EXACTLY ONE run. `_manual` overrides the gate and is cleared inside
        # the cron; the timestamp drives the pending-state TTL.
        ICP.set_param('era_seo.article_pending', 'True')
        ICP.set_param(
            'era_seo.article_pending_started_at',
            fields.Datetime.to_string(fields.Datetime.now()),
        )
        ICP.set_param('era_seo.article_generator_manual', 'True')
        web = self.env['website'].sudo().search([], limit=1)
        slang = web.default_lang_id.code if web and web.default_lang_id else None
        ICP.set_param(
            'era_seo.article_progress',
            'جارٍ التحضير…' if (slang or '')[:2].lower() == 'ar' else 'Preparing…')
        if cron:
            try:
                cron.sudo()._trigger()  # Odoo 17+ "fire this cron now"
            except Exception:  # noqa: BLE001
                _logger.exception('Generate Now: cron _trigger failed')
        self.invalidate_recordset(['is_article_pending'])
        # soft_reload re-reads is_article_pending without losing the open tab /
        # scroll, flipping the button to the spinner banner immediately.
        return reload_action

    @api.model
    def stop_bulk_ai_fill(self):
        """Cancel an in-progress bulk run. Cursors are kept where they are so
        the next start picks up rather than re-scans (callers that want a
        fresh start should use start_bulk_ai_fill which resets them)."""
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.bulk_ai_fill_active', 'False')

    def action_start_bulk_ai_fill(self):
        """Hub-button wrapper: start the bulk-fill cron sweep and refresh
        the form so the running-state alert + button visibility update.
        """
        self.ensure_one()
        self.sudo().start_bulk_ai_fill()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_stop_bulk_ai_fill(self):
        """Hub-button wrapper: pause the bulk-fill cron sweep. Cursors are
        kept where they are so a later restart resumes mid-queue.
        """
        self.ensure_one()
        self.sudo().stop_bulk_ai_fill()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    # ------------------------------------------------------------------------
    # Background bulk Auto-Fix — run-level "Auto-Fix (AI)" button
    # ------------------------------------------------------------------------
    #
    # The run button used to suggest+apply EVERY finding synchronously inside
    # the HTTP request. A run with more than ~10 findings makes that many slow
    # AI calls back-to-back and runs past the worker's limit_time_real (1200s),
    # so the worker is killed mid-batch. The button now only flips
    # `era_seo.bulk_fix_active` (+ records the run id) and fires this cron,
    # which drains the queue OUT of the request in budgeted chunks. Each fix is
    # committed as it lands (action_ai_suggest / action_ai_apply commit per
    # record), so a killed tick never loses work and the next tick resumes.
    #
    # State (ir.config_parameter, no schema change):
    #   era_seo.bulk_fix_active   bool  — cron gate + re-entrancy guard
    #   era_seo.bulk_fix_run_id   int   — run whose findings to drain (0 = all)
    #   era_seo.bulk_fix_last_id  int   — high-water finding id within the sweep

    # Suggest+apply this many findings per chunk; the wall-clock budget is
    # checked between chunks so a slow/retry-heavy provider can't run one tick
    # past limit_time_real. Budget + one chunk's worst case stays under 1200s.
    _BULK_FIX_CHUNK = 5
    _BULK_FIX_TICK_BUDGET_S = 600

    @api.model
    def cron_bulk_ai_fix(self):
        """Drain the run-level Auto-Fix queue in the background.

        No-op until `era_seo.bulk_fix_active` is True. Re-entrancy is covered
        twice over: the flag stops a second sweep being enqueued while one is
        active (see era.seo.audit.run.action_ai_fix_findings), and Odoo
        serialises a given cron (SELECT ... FOR UPDATE NOWAIT) so two ticks of
        this cron can never overlap. A high-water finding id guarantees the
        loop always advances — even past a finding whose target vanished (which
        action_ai_suggest skips without setting a status) — so it terminates.
        """
        import time as _time
        if _icp_get(self.env, 'era_seo.bulk_fix_active') not in _TRUE:
            return
        # Confirm the provider is reachable ONCE per tick. If it's down, keep
        # the flag on and bail — the next tick retries when it's back — rather
        # than burning the whole queue into 'failed'.
        from .ai_client import AIClient
        from .seo_audit_finding_ai import AI_FIXABLE_CODES
        ok, reason = AIClient(self.env).is_available()
        if not ok:
            _logger.warning(
                'bulk_ai_fix: AI provider unavailable (%s) — pausing this '
                'tick, flag kept on for retry', reason)
            return

        deadline = _time.monotonic() + self._BULK_FIX_TICK_BUDGET_S
        ICP = self.env['ir.config_parameter'].sudo()
        Finding = self.env['era.seo.audit.finding'].sudo()
        try:
            run_id = int(ICP.get_param('era_seo.bulk_fix_run_id', '0') or '0')
        except (TypeError, ValueError):
            run_id = 0
        try:
            last_id = int(ICP.get_param('era_seo.bulk_fix_last_id', '0') or '0')
        except (TypeError, ValueError):
            last_id = 0

        # Only untouched ('none') AI-fixable findings — a processed finding
        # leaves 'none', and the high-water id below skips anything we've
        # already handled this sweep (incl. status-less vanished targets).
        static_domain = [
            ('is_resolved', '=', False),
            ('ai_status', '=', 'none'),
            ('check_code', 'in', list(AI_FIXABLE_CODES)),
        ]
        if run_id:
            static_domain.append(('run_id', '=', run_id))

        done = 0
        drained = False
        while True:
            if _time.monotonic() > deadline:
                break
            chunk = Finding.search(
                [('id', '>', last_id)] + static_domain,
                limit=self._BULK_FIX_CHUNK, order='id')
            if not chunk:
                drained = True
                break
            try:
                # System context bypasses the per-user SEO-Manager gate (the
                # admin opted in by clicking Auto-Fix); the method commits each
                # finding as it's suggested+applied.
                chunk.with_context(
                    _era_ai_system=True).action_ai_suggest_and_apply()
            except Exception:  # noqa: BLE001
                # Clear any aborted-transaction state; the per-record commits
                # already persisted the good work, so we only lose the (failed)
                # tail of this chunk.
                self.env.cr.rollback()
                _logger.exception(
                    'bulk_ai_fix: chunk failed (ids %s)', chunk.ids)
            # Advance the high-water past the whole chunk on a side cursor
            # (same shared-row 40001 hazard as bulk_ai_fill), whether or not
            # each record succeeded, so one bad record can't stall the queue.
            last_id = max(chunk.ids)
            self._era_commit_icp({'era_seo.bulk_fix_last_id': str(last_id)})
            done += len(chunk)

        if drained:
            self._era_commit_icp({'era_seo.bulk_fix_active': 'False'})
            _logger.info(
                'bulk_ai_fix: queue drained (%d finding(s) this tick)', done)
        else:
            _logger.info(
                'bulk_ai_fix: tick budget (%ds) reached — %d done, resuming',
                self._BULK_FIX_TICK_BUDGET_S, done)
            # Chain immediately instead of waiting for the 2-minute schedule so
            # a big backlog drains promptly. The NOWAIT lock still prevents
            # overlap with the currently-finishing tick.
            cron = self.env.ref(
                'era_seo_suite.cron_bulk_ai_fix', raise_if_not_found=False)
            if cron:
                try:
                    cron.sudo()._trigger()
                except Exception:  # noqa: BLE001
                    _logger.exception('bulk_ai_fix: self-trigger failed')

    @api.model
    def stop_bulk_ai_fix(self):
        """Cancel an in-progress background Auto-Fix sweep (flag off; the
        high-water is left where it is)."""
        self._era_commit_icp({'era_seo.bulk_fix_active': 'False'})

    # ------------------------------------------------------------------------
    # Weekly audit + auto AI-fix
    # ------------------------------------------------------------------------

    # Check codes the UNATTENDED audit cron is allowed to auto-apply. Limited
    # to fixes with NO visible content change: OG image (set to the company
    # logo) and JSON-LD schema (structured data, invisible to readers). Other
    # ai_supported codes stay MANUAL via the finding's AI buttons —
    # thin_content appends AI-written text to the page, and title/slug rewrites
    # change user-visible copy, so neither should fire unattended.
    _UNATTENDED_FIX_CODES = {'missing_og_image', 'missing_schema'}
    # Max findings SELECTED per tick. Schema fixes are AI calls; a large
    # backlog processed in one tick could run past limit_time_real and get the
    # worker killed. The remainder is picked up on the next tick.
    _UNATTENDED_FIX_BATCH = 25
    # Within a tick, suggest+apply this many findings at a time so the wall-
    # clock budget below is checked between small chunks rather than only once.
    _UNATTENDED_FIX_CHUNK = 5
    # Per-tick wall-clock budget (seconds). We stop entering NEW chunks once
    # this is spent — the in-flight chunk still finishes — so a slow/retry-heavy
    # AI provider can't push one tick past limit_time_real (1200s). Budget +
    # one chunk's worst case stays comfortably under that ceiling.
    _UNATTENDED_FIX_TICK_BUDGET_S = 600

    @api.model
    def cron_weekly_audit_and_fix(self):
        """WEEKLY: run a fresh SEO audit (the heavy full re-scan) and auto-apply
        the SAFE fixes for anything it newly finds.

        The full audit stays WEEKLY by design — it re-scans every page (now
        including a rendered-HTML fetch). The lighter day-to-day clearing of
        the safe-fix backlog runs separately in ``cron_daily_ai_fix`` so the
        audit doesn't run daily. Only ``_UNATTENDED_FIX_CODES`` (OG image,
        JSON-LD schema) are auto-applied, batched by ``_UNATTENDED_FIX_BATCH``.

        Gated by `era_seo.weekly_audit_active` (default True).
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
        findings = run.finding_ids.filtered(
            lambda f: f.ai_status == 'none'
            and f.check_code in self._UNATTENDED_FIX_CODES
        )[:self._UNATTENDED_FIX_BATCH]
        self._apply_safe_fixes(findings, 'weekly_audit_and_fix')

    @api.model
    def cron_daily_ai_fix(self):
        """DAILY: apply the SAFE auto-fixes (OG image, JSON-LD schema) to the
        backlog of open, untouched findings WITHOUT running a fresh audit.

        This is the day-to-day backlog clearer. The full re-scan stays on the
        weekly ``cron_weekly_audit_and_fix``; here we only suggest+apply on
        existing open findings, batched by ``_UNATTENDED_FIX_BATCH`` so a
        backlog of schema (AI) fixes can't run one tick past
        limit_time_real_cron. Same `era_seo.weekly_audit_active` gate.
        """
        if _icp_get(self.env, 'era_seo.weekly_audit_active', 'True') not in _TRUE:
            return
        findings = self.env['era.seo.audit.finding'].sudo().search([
            ('is_resolved', '=', False),
            ('ai_status', '=', 'none'),
            ('check_code', 'in', list(self._UNATTENDED_FIX_CODES)),
        ], limit=self._UNATTENDED_FIX_BATCH)
        self._apply_safe_fixes(findings, 'daily_ai_fix')

    # ------------------------------------------------------------------
    # Monthly sitemap rebuild + link health
    # ------------------------------------------------------------------

    _SITEMAP_FETCH_TIMEOUT_S = 10
    _SITEMAP_MAX_URLS = 5000
    _SITEMAP_BUDGET_S = 1500   # wall-clock cap for the whole validation pass

    def cron_monthly_sitemap_rebuild(self):
        """MONTHLY: rebuild the sitemap, verify every link still resolves, prune
        pages that are gone, and report broken internal links.

        1. Drop the cached sitemap attachments and regenerate them fresh (Odoo
           lists only PUBLISHED, existing pages, so deleted/unpublished pages
           leave the sitemap automatically).
        2. GET every URL in the fresh sitemap; 404/410 means a dead page — when
           pruning is on, unpublish the matching website.page so it leaves the
           site and the next sitemap, then rebuild once more.
        3. Scan published pages' content for internal links and report any that
           are broken ('missed links') — logged, not auto-edited.

        Gates (ir.config_parameter):
          era_seo.sitemap_cron_active    default True  — master switch
          era_seo.sitemap_prune_enabled  default True  — unpublish dead pages
        """
        if _icp_get(self.env, 'era_seo.sitemap_cron_active', 'True') not in _TRUE:
            return
        base = (self.env['ir.config_parameter'].sudo()
                .get_param('web.base.url') or '').rstrip('/')
        if not base:
            _logger.warning('ERA SEO sitemap cron: web.base.url not set; skipping.')
            return

        import json as _json
        import time as _time
        deadline = _time.monotonic() + self._SITEMAP_BUDGET_S

        self._sitemap_clear_cache()
        urls = self._sitemap_collect_urls(base)            # regenerates + caches
        summary = self._sitemap_validate_and_prune(base, urls, deadline)
        if summary.get('pruned'):
            self._sitemap_clear_cache()                    # drop pruned pages
            self._sitemap_collect_urls(base)
        summary['missed_links'] = self._sitemap_missed_links(base, urls, deadline)

        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('era_seo.sitemap_last_run',
                      fields.Datetime.to_string(fields.Datetime.now()))
        ICP.set_param('era_seo.sitemap_last_summary', _json.dumps(summary))
        _logger.info('ERA SEO monthly sitemap rebuild: %s', summary)
        return summary

    def _sitemap_clear_cache(self):
        """Delete cached sitemap attachments so the next request regenerates a
        fresh sitemap (which excludes deleted / unpublished pages)."""
        atts = self.env['ir.attachment'].sudo().search(
            [('name', '=like', '/sitemap%')])
        n = len(atts)
        if n:
            atts.unlink()
            self.env.cr.commit()   # durable before we re-fetch to regenerate
        _logger.info('ERA SEO sitemap: cleared %d cached attachment(s).', n)
        return n

    def _sitemap_collect_urls(self, base):
        """Fetch the sitemap fresh over HTTP (regenerating + re-caching it) and
        return every page path in it, following the index to child sitemaps."""
        import re as _re
        import requests as _requests
        from urllib.parse import urlparse as _urlparse
        out, seen = [], set()
        headers = {'User-Agent': 'ERA-SEO-Sitemap/1.0'}

        def _fetch(url, depth=0):
            if depth > 3 or url in seen:
                return
            seen.add(url)
            try:
                r = _requests.get(url, timeout=self._SITEMAP_FETCH_TIMEOUT_S,
                                  headers=headers)
                if r.status_code != 200 or not r.text:
                    return
                text = r.text
            except Exception as exc:  # noqa: BLE001
                _logger.warning('ERA SEO sitemap: fetch %s failed (%s)', url, exc)
                return
            for loc in _re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', text):
                path = _urlparse(loc).path or loc
                if path.endswith('.xml') and 'sitemap' in path:
                    _fetch(loc, depth + 1)          # child sitemap
                elif path.startswith('/'):
                    out.append(path)

        _fetch(base + '/sitemap.xml')
        return list(dict.fromkeys(out))

    def _sitemap_validate_and_prune(self, base, urls, deadline):
        """GET each sitemap URL; unpublish pages that are gone (404/410) when
        pruning is enabled. Returns a summary dict."""
        import time as _time
        import requests as _requests
        prune = _icp_get(self.env, 'era_seo.sitemap_prune_enabled',
                         'True') in _TRUE
        Page = self.env['website.page'].sudo()
        headers = {'User-Agent': 'ERA-SEO-Sitemap/1.0'}
        checked = broken = pruned = errors = 0
        broken_urls = []
        for path in urls[:self._SITEMAP_MAX_URLS]:
            if _time.monotonic() > deadline:
                _logger.warning('ERA SEO sitemap: validation budget hit at %d/%d.',
                                checked, len(urls))
                break
            try:
                resp = _requests.get(
                    base + path, timeout=self._SITEMAP_FETCH_TIMEOUT_S,
                    allow_redirects=False, headers=headers)
                code = resp.status_code
            except Exception:  # noqa: BLE001
                errors += 1
                continue
            checked += 1
            if code in (404, 410):
                broken += 1
                broken_urls.append(path)
                if prune:
                    dead = Page.search(
                        [('url', '=', path), ('is_published', '=', True)])
                    if dead:
                        dead.write({'is_published': False})
                        pruned += len(dead)
                        _logger.info(
                            'ERA SEO sitemap: unpublished dead page %s (HTTP %s).',
                            path, code)
        return {
            'in_sitemap': len(urls), 'checked': checked, 'broken': broken,
            'pruned': pruned, 'errors': errors, 'broken_urls': broken_urls[:50],
        }

    def _sitemap_missed_links(self, base, sitemap_urls, deadline):
        """Find broken internal links inside published pages' content. Reports
        (logs) them; never edits content automatically. Returns a summary."""
        import re as _re
        import time as _time
        import requests as _requests
        from urllib.parse import urlparse as _urlparse
        IrHttp = self.env.get('ir.http')
        ok = set(u.rstrip('/') for u in sitemap_urls)
        site = self.env['website'].sudo().search([], limit=1)
        lang = site.default_lang_id.code if site and site.default_lang_id else None
        Page = self.env['website.page'].sudo()
        if lang:
            Page = Page.with_context(lang=lang)
        # Collect unique internal link targets not already known-good.
        candidates = {}
        for pg in Page.search([('is_published', '=', True)]):
            html = pg.arch or ''
            for href in _re.findall(r'href=["\']([^"\']+)["\']', html):
                path = (_urlparse(href).path or '').strip()
                if not path.startswith('/') or path.rstrip('/') in ok:
                    continue
                if IrHttp is not None and (IrHttp._era_is_asset_like(path)
                                           or IrHttp._era_is_system_path(path)):
                    continue
                candidates.setdefault(path, pg.url or pg.name)
        headers = {'User-Agent': 'ERA-SEO-Sitemap/1.0'}
        dead = []
        for path, src in list(candidates.items())[:self._SITEMAP_MAX_URLS]:
            if _time.monotonic() > deadline:
                break
            try:
                resp = _requests.get(base + path, allow_redirects=False,
                                     timeout=self._SITEMAP_FETCH_TIMEOUT_S,
                                     headers=headers)
                if resp.status_code in (404, 410):
                    dead.append({'url': path, 'on_page': src})
                    _logger.info('ERA SEO sitemap: missed link %s (on %s).',
                                 path, src)
            except Exception:  # noqa: BLE001
                continue
        return {'scanned': len(candidates), 'broken': len(dead),
                'links': dead[:50]}

    def _apply_safe_fixes(self, findings, tag):
        """Shared suggest+apply step for the auto-fix crons.

        Processes findings in small chunks under a per-tick wall-clock budget
        so a slow/retry-heavy AI provider can't run one cron tick past
        limit_time_real and get the worker killed: we stop entering NEW chunks
        once the budget is spent (the in-flight chunk finishes) and the
        remainder is picked up on the next tick. Chunking also isolates a
        failing finding to its chunk instead of aborting the whole batch.
        """
        if not findings:
            _logger.info('%s: no untouched safe-fixable findings (%s)',
                         tag, ', '.join(sorted(self._UNATTENDED_FIX_CODES)))
            return
        import time as _time
        deadline = _time.monotonic() + self._UNATTENDED_FIX_TICK_BUDGET_S
        done = 0
        total = len(findings)
        for i in range(0, total, self._UNATTENDED_FIX_CHUNK):
            if _time.monotonic() > deadline:
                _logger.info(
                    '%s: tick budget (%ds) reached — processed %d/%d, '
                    'remainder next tick', tag,
                    self._UNATTENDED_FIX_TICK_BUDGET_S, done, total)
                break
            chunk = findings[i:i + self._UNATTENDED_FIX_CHUNK]
            try:
                chunk.with_context(
                    _era_ai_system=True).action_ai_suggest_and_apply()
                done += len(chunk)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    '%s: AI suggest+apply failed on a chunk of %d',
                    tag, len(chunk))
        if done:
            _logger.info('%s: auto-fixed %d finding(s) this tick', tag, done)

    # ------------------------------------------------------------------------
    # Auto-publish: trend-aware blog article every N days
    # ------------------------------------------------------------------------

    @api.model
    def cron_generate_blog_article(self):
        """Wrapper that keeps the pending flag consistent across success,
        failure and re-entrancy. The actual work lives in `_run_article_gen`.

        The flag set/clear writes are funnelled through a **separate
        cursor** that commits immediately, so a concurrent writer on
        the same ICP rows (e.g. a user clicking Generate again, or the
        TTL clear path firing) can't roll back the cron's main
        transaction. Without this isolation, postgres's SerializationFailure
        on the flag row aborts the whole cron — including any blog post
        the AI just generated. We saw that exact failure in prod
        (commit message references it). The cron's main transaction
        only commits the actual blog work; the flag is metadata that
        moves on its own.
        """
        self._set_article_pending(True)
        try:
            return self._run_article_gen()
        except Exception:  # noqa: BLE001
            # CRITICAL: swallow so the cron job COMMITS and its queued trigger
            # is consumed. If the exception propagated, Odoo would roll the
            # whole cron transaction back — the trigger would NOT be consumed
            # and would re-fire every minute, looping "writing (1)" forever
            # (the bug behind "5+ rounds"). Log it; the next scheduled run
            # retries on its own.
            _logger.exception(
                'cron_generate_blog_article: generation failed; consuming the '
                'trigger to prevent a re-fire loop')
            return False
        finally:
            # Always clear — even on no-op runs (gate off, AI unavailable).
            # The UI's spinner stops on the next poll tick.
            self._set_article_pending(False)
            self._set_article_progress('')

    @api.model
    def _set_article_pending(self, pending):
        """Write the article-pending flag via a short-lived cursor that
        commits immediately. Insulates the cron's main transaction from
        SerializationFailure on the shared ICP row.

        On any error we swallow silently and fall back to a same-cursor
        write — losing isolation is better than losing the cron's work.
        """
        from odoo import sql_db
        try:
            with sql_db.db_connect(self.env.cr.dbname).cursor() as side_cr:
                ICP = self.env(cr=side_cr)['ir.config_parameter'].sudo()
                ICP.set_param(
                    'era_seo.article_pending', 'True' if pending else 'False')
                if pending:
                    ICP.set_param(
                        'era_seo.article_pending_started_at',
                        fields.Datetime.to_string(fields.Datetime.now()),
                    )
                else:
                    ICP.set_param('era_seo.article_pending_started_at', '')
                side_cr.commit()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                '_set_article_pending(%s): side-cursor write failed (%s); '
                'falling back to in-transaction write', pending, exc)
            ICP = self.env['ir.config_parameter'].sudo()
            ICP.set_param(
                'era_seo.article_pending', 'True' if pending else 'False')
            if pending:
                ICP.set_param(
                    'era_seo.article_pending_started_at',
                    fields.Datetime.to_string(fields.Datetime.now()),
                )
            else:
                ICP.set_param('era_seo.article_pending_started_at', '')

    def _era_commit_icp(self, params):
        """Write one or more ir.config_parameter keys via a short-lived cursor
        that commits immediately, insulating the caller's (long cron) main
        transaction from SerializationFailure (40001) on the shared ICP row.

        Odoo cursors run at REPEATABLE READ (odoo/sql_db.py), so a 40001 on a
        hot row aborts the WHOLE transaction and a SAVEPOINT cannot clear it —
        only a separate, independently-committed connection sidesteps the
        conflict. ``params`` is a {key: str_value} mapping. On side-cursor
        failure we fall back to an in-transaction write (losing isolation is
        better than losing the write). Generalizes ``_set_article_pending``.
        """
        from odoo import sql_db
        try:
            with sql_db.db_connect(self.env.cr.dbname).cursor() as side_cr:
                ICP = self.env(cr=side_cr)['ir.config_parameter'].sudo()
                for key, value in params.items():
                    ICP.set_param(key, value)
                side_cr.commit()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                '_era_commit_icp(%s): side-cursor write failed (%s); '
                'falling back to in-transaction write', list(params), exc)
            ICP = self.env['ir.config_parameter'].sudo()
            for key, value in params.items():
                ICP.set_param(key, value)

    def _set_article_progress(self, msg):
        """Publish a one-line generation step (e.g. "Searching trends…") on a
        side cursor that commits immediately, so the form's pending banner shows
        it live mid-generation — the cron's own long transaction won't be visible
        to the polling form until it commits, so we can't write this inline."""
        self._era_commit_icp({'era_seo.article_progress': msg or ''})

    @staticmethod
    def _article_progress_msgs(lang_code):
        """Step labels in the site's language (ar/en — falls back to en). Kept
        here, not _()-wrapped, because the cron writes them in the cron user's
        language, not the viewer's; rendering in the site language is what users
        actually want to read in the banner."""
        ar = (lang_code or '')[:2].lower() == 'ar'
        return {
            'domain':  'اختيار مجال جديد…'        if ar else 'Choosing a fresh topic area…',
            'trends':  'البحث عن الترند…'          if ar else 'Searching trends…',
            'writing': 'كتابة المقال'             if ar else 'Writing the article',
            'extend':  'إطالة المحتوى'            if ar else 'Expanding the article',
            'image':   'إنشاء صورة الغلاف…'        if ar else 'Creating the cover image…',
            'publish': 'حفظ ونشر المقال…'         if ar else 'Saving the article…',
        }

    @api.model
    def _pick_article_focus_category(self, BlogPost):
        """Steps 1-3 of the rebuilt pipeline: the site's DOMAINS are its blog
        categories; read the last 5 posts; choose a domain NOT covered by them.

        Deterministic (DB-agnostic) so generation can't fixate on one field
        regardless of how the business summary is worded: the last 5 posts'
        categories are EXCLUDED, and among the remaining domains the
        least-recently-used one is picked so coverage keeps spreading. Returns a
        category name, or None when there's too little taxonomy to rotate (then
        the prompt-level diversity rules take over).
        """
        Category = self.env.get('era.blog.category')
        if Category is None or BlogPost is None:
            return None
        cats = Category.sudo().search([])              # 1: the site's domains
        if len(cats) < 2:
            return None
        # 2-3: read the last 5 posts, EXCLUDE the domains they covered. If every
        # category appears in the last 5 (tiny taxonomy), fall back to all.
        last5 = BlogPost.sudo().search(
            [('era_category_id', '!=', False)], order='id desc', limit=5)
        recent_cat_ids = set(last5.mapped('era_category_id').ids)
        candidates = cats.filtered(lambda c: c.id not in recent_cat_ids) or cats
        # Among the eligible domains, pick the LEAST-RECENTLY-USED (max post id
        # per category; never-used = 0 sorts first) to keep spreading coverage.
        last_used = {}
        try:
            for cat, max_id in BlogPost.sudo()._read_group(
                    [('era_category_id', '!=', False)],
                    groupby=['era_category_id'], aggregates=['id:max']):
                if cat:
                    last_used[cat.id] = max_id or 0
        except Exception:  # noqa: BLE001 — degrade to prompt-only diversity
            pass
        chosen = min(candidates, key=lambda c: last_used.get(c.id, 0))
        _logger.info(
            'article generator: %d domains, excluded last-5 %s, chose "%s"',
            len(cats), sorted(recent_cat_ids), chosen.name)
        return chosen.name

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
          3. Collect real internal URLs + GSC query opportunities, then ask
             the AI agent for {title, content_html, seo meta, suggested
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
        recent_posts = BlogPost.sudo().search([], order='id desc', limit=30)
        past_titles = recent_posts.mapped('name')
        # The LAST 5 posts' subject+category — passed as an explicit OFF-LIMITS
        # list so the generator can't keep producing the same field (e.g. the
        # blog had drifted entirely to "factories").
        recent_subjects = [
            {'title': (p.name or '').strip(),
             'category': (p.era_category_id.name
                          if 'era_category_id' in p._fields and p.era_category_id
                          else '')}
            for p in recent_posts[:5] if (p.name or '').strip()
        ]
        # GENERAL anti-fixation: deterministically rotate the article's topic
        # across the site's OWN blog categories (least-recently-used first),
        # independent of how the business summary is worded. Works on any DB;
        # falls back to prompt-only diversity when there are <2 categories.
        focus_category = self._pick_article_focus_category(BlogPost)
        existing_categories = Category.sudo().search([], limit=50).mapped('name')
        related_pages = self._article_internal_link_targets()
        # 5: article language = the WEBSITE's default (served) language. This is
        # the fix for English articles on an Arabic site — we no longer rely on
        # a possibly-empty/stale article_lang setting; the live site language
        # wins, with the setting only as an explicit override and auto-detect
        # as the last resort.
        web = self.env['website'].sudo().search([], limit=1)
        site_lang = (web.default_lang_id.code
                     if web and web.default_lang_id else None)
        lang_code = (site_lang
                     or (ICP.get_param('era_seo.article_lang', '') or '').strip()
                     or None)
        # Live progress shown in the form banner, step by step.
        steps = self._article_progress_msgs(lang_code)
        self._set_article_progress(steps['trends'])
        search_opportunities = self._article_search_opportunities()
        # Real trends signal: today's top Google Trends for the configured geo.
        # Empty when offline / blocked / unparseable — the agent then falls
        # back to its own "what's relevant now" reasoning.
        trending_now = self._fetch_google_trends()
        prompt_addendum = (ICP.get_param('era_seo.article_prompt_addendum', '') or '').strip() or None
        self._set_article_progress(steps['writing'])
        try:
            article = client.propose_article(
                business_context, past_titles, existing_categories,
                lang_code=lang_code,
                trending_now=trending_now,
                prompt_addendum=prompt_addendum,
                related_pages=related_pages,
                search_opportunities=search_opportunities,
                recent_subjects=recent_subjects,
                focus_category=focus_category,
                progress=self._set_article_progress,
                msg_writing=steps['writing'], msg_extend=steps['extend'])
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

        # Deterministic rotation wins: file under the assigned category even if
        # the model's returned `category` drifted, so coverage actually spreads.
        if focus_category:
            article['category'] = focus_category

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

        self._set_article_progress(steps['publish'])
        post = BlogPost.sudo().create(post_vals)

        # Hero image — call the hook, attach to all relevant slots if it
        # returned bytes.
        self._set_article_progress(steps['image'])
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

    @api.model
    def _article_internal_link_targets(self, exclude_post_ids=None, limit=16):
        """Return existing site URLs the article generator may link to.

        The AI prompt is explicitly told to use these only when relevant and
        never invent URLs. Supplying the list here gives generated articles a
        chance to connect to real service pages and older posts, which helps
        readers continue their task and gives crawlers clearer site context.
        """
        exclude_post_ids = set(exclude_post_ids or [])
        targets = []

        def _add(title, url):
            title = (title or '').strip()
            url = (url or '').strip()
            if not title or not url or url == '#':
                return
            if not (
                url.startswith('/') or
                url.startswith('http://') or
                url.startswith('https://')
            ):
                return
            if any(t['url'] == url for t in targets):
                return
            targets.append({'title': title, 'url': url})

        Page = self.env.get('website.page')
        if Page is not None:
            try:
                published_field = (
                    'website_published'
                    if 'website_published' in Page._fields
                    else 'is_published'
                )
                pages = Page.sudo().search(
                    [(published_field, '=', True), ('url', '!=', False)],
                    order='write_date desc, id desc',
                    limit=max(4, limit // 2))
            except Exception:  # noqa: BLE001
                pages = Page.sudo().browse()
            for page in pages:
                _add(getattr(page, 'seo_title', False) or page.name, page.url)

        BlogPost = self.env.get('blog.post')
        if BlogPost is not None:
            domain = [('is_published', '=', True)]
            if exclude_post_ids:
                domain.append(('id', 'not in', list(exclude_post_ids)))
            posts = BlogPost.sudo().search(
                domain,
                order='published_date desc, id desc',
                limit=max(4, limit - len(targets)))
            for post in posts:
                _add(
                    getattr(post, 'seo_title', False) or post.name,
                    getattr(post, 'website_url', '') or '',
                )

        return targets[:limit]

    @api.model
    def _article_search_opportunities(self, limit=12):
        """Return high-impression Search Console queries from the last 28 days.

        These are used only as prompt context. The article prompt treats them
        as demand signals, not as instructions to stuff exact-match keywords.
        """
        GscQuery = self.env.get('era.gsc.query')
        if GscQuery is None:
            return []
        from datetime import date, timedelta
        since = date.today() - timedelta(days=28)
        rows = GscQuery.sudo().search(
            [('date', '>=', since), ('impressions', '>', 0)],
            order='impressions desc, clicks asc',
            limit=250)
        grouped = {}
        for row in rows:
            key = (row.query or '').strip().lower()
            if not key:
                continue
            bucket = grouped.setdefault(key, {
                'query': row.query.strip(),
                'clicks': 0,
                'impressions': 0,
                'weighted_position': 0.0,
            })
            impressions = int(row.impressions or 0)
            bucket['clicks'] += int(row.clicks or 0)
            bucket['impressions'] += impressions
            bucket['weighted_position'] += float(row.position or 0.0) * impressions

        opportunities = []
        for item in grouped.values():
            impressions = item['impressions']
            if not impressions:
                continue
            position = item['weighted_position'] / impressions
            # Favor queries with demand and room to improve.
            score = (
                impressions * (1.0 + min(position, 30.0) / 30.0) -
                item['clicks'] * 20
            )
            opportunities.append({
                'query': item['query'],
                'clicks': item['clicks'],
                'impressions': impressions,
                'position': round(position, 2),
                '_score': score,
            })
        opportunities.sort(key=lambda i: (-i['_score'], -i['impressions']))
        for item in opportunities:
            item.pop('_score', None)
        return opportunities[:limit]

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
          - 'none'       → returns None
          - 'openai'     → OpenAI's image-generation API (DALL-E / gpt-image-1)
          - 'openrouter' → OpenRouter (any image-capable model)

        Override this method in a custom addon for additional providers;
        return raw bytes or None.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        provider = (
            ICP.get_param('era_seo.image_provider', 'none') or 'none'
        ).strip().lower()
        if provider == 'openai':
            return self._generate_image_openai(prompt)
        if provider == 'openrouter':
            return self._generate_image_openrouter(prompt)
        return None

    def _generate_image_openrouter(self, prompt):
        """Call OpenRouter for an image-capable model and return raw bytes.

        OpenRouter routes image generation through the OpenAI-compatible
        ``/api/v1/chat/completions`` endpoint — image-capable models
        return the rendered image inside the assistant message rather
        than via a dedicated ``/images/generations`` endpoint. We send
        the prompt as a user message and probe for the image in a few
        known response shapes (top-level ``images``, content ``image_url``
        parts, base64 ``data:`` URLs).

        Reads the API key from ``era_seo.image_openrouter_key`` and the
        model from ``era_seo.image_model`` (e.g.
        ``google/gemini-2.5-flash-image-preview``). Any failure logs a
        WARNING and returns None.
        """
        import requests as _requests
        import base64 as _base64
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = (ICP.get_param('era_seo.image_openrouter_key', '') or '').strip()
        if not api_key:
            _logger.warning(
                'image-gen openrouter: no API key configured (Blog Gen '
                'tab → OpenRouter key); skipping')
            return None
        model = (ICP.get_param('era_seo.image_model', '') or '').strip()
        if not model:
            _logger.warning(
                'image-gen openrouter: no model configured (Blog Gen tab '
                '→ Image model, e.g. google/gemini-2.5-flash-image-preview); '
                'skipping')
            return None
        body = {
            'model': model,
            'messages': [{
                'role': 'user',
                'content': (prompt or '')[:4000],
            }],
        }
        # `modalities` is a Gemini-specific hint. Other image models on
        # OpenRouter (Flux, xAI Grok Imagine, etc.) reject it with
        # HTTP 404 "No endpoints found that support the requested output
        # modalities: image, text". Only send it for models that need it.
        if 'gemini' in model.lower() and 'image' in model.lower():
            body['modalities'] = ['image', 'text']
        try:
            r = _requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': 'Bearer %s' % api_key,
                    'Content-Type': 'application/json',
                },
                json=body, timeout=600,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning('image-gen openrouter: network error: %s', exc)
            return None
        if r.status_code >= 400:
            _logger.warning(
                'image-gen openrouter: HTTP %s — %s', r.status_code,
                (r.text or '')[:300].replace('\n', ' '))
            return None
        try:
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            _logger.warning('image-gen openrouter: malformed 2xx: %s', exc)
            return None

        # Walk the response shapes OpenRouter image models are known to
        # use. Return the first image we can decode.
        def _try_url(url):
            if not url:
                return None
            # Embedded base64 ("data:image/png;base64,...").
            if url.startswith('data:'):
                try:
                    return _base64.b64decode(url.split(',', 1)[1])
                except Exception:  # noqa: BLE001
                    return None
            # Plain HTTPS — download.
            try:
                dl = _requests.get(url, timeout=600)
                dl.raise_for_status()
                return dl.content
            except Exception:  # noqa: BLE001
                return None

        try:
            message = payload['choices'][0]['message']
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'image-gen openrouter: response had no choices[0].message: %s', exc)
            return None

        # Shape A: message.images = [{type: 'image_url', image_url: {...}}, ...]
        for item in (message.get('images') or []):
            if isinstance(item, dict):
                img = item.get('image_url') or item.get('url') or item
                if isinstance(img, dict):
                    bytes_ = _try_url(img.get('url'))
                elif isinstance(img, str):
                    bytes_ = _try_url(img)
                else:
                    bytes_ = None
                if bytes_:
                    return bytes_

        # Shape B: message.content is a list of parts, with image_url parts.
        content = message.get('content')
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get('type') == 'image_url':
                    img = part.get('image_url')
                    url = img.get('url') if isinstance(img, dict) else img
                    bytes_ = _try_url(url)
                    if bytes_:
                        return bytes_
                if part.get('type') in ('image', 'output_image') and part.get('source'):
                    src = part['source']
                    if isinstance(src, dict) and src.get('data'):
                        try:
                            return _base64.b64decode(src['data'])
                        except Exception:  # noqa: BLE001
                            pass

        # Shape C: a plain string content that contains a data: URL.
        if isinstance(content, str) and content.startswith('data:'):
            bytes_ = _try_url(content)
            if bytes_:
                return bytes_

        _logger.warning(
            'image-gen openrouter: response had no recognisable image '
            'field for model=%s. Snippet=%r', model, str(payload)[:300])
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
        model = (ICP.get_param('era_seo.image_model', 'gpt-image-2') or 'gpt-image-2').strip()
        # Normalize the size string. Admins copy/paste from docs and end up
        # with the Unicode multiplication sign U+00D7 ('×') instead of the
        # ASCII 'x' OpenAI requires. Also fix the fullwidth variants for
        # completeness, then lowercase.
        size = (ICP.get_param('era_seo.image_size', '1024x1024') or '1024x1024').strip()
        for ch in ('×', '✕', '✖', 'ｘ', 'Ｘ'):
            size = size.replace(ch, 'x')
        size = size.replace('X', 'x')
        # Quality tier — only gpt-image-1 honours this; dall-e-* models
        # reject it as an unknown parameter. We send it only when the
        # active (or fallback-chain) model is gpt-image-1 family.
        quality = (ICP.get_param('era_seo.image_quality', 'low') or 'low').strip().lower()
        if quality not in ('low', 'medium', 'high', 'auto'):
            quality = 'low'

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
            # quality is gpt-image-1-only; dall-e-* would 400.
            if call_model.startswith('gpt-image'):
                body['quality'] = quality
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

    def action_ai_traffic_advice(self):
        """Open the AI traffic-advice dialog with a fresh tip already generated."""
        wiz = self.env['era.seo.advice.wizard'].create({})
        wiz._generate()
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Traffic Advice'),
            'res_model': 'era.seo.advice.wizard',
            'res_id': wiz.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _era_read_action(self, xmlid):
        action = self.env.ref(xmlid, raise_if_not_found=False)
        if not action:
            raise UserError(_('The requested action is not available.'))
        return action.read()[0]

    def action_open_priority_findings(self):
        """Open unresolved critical/warning findings from the dashboard."""
        self.ensure_one()
        action = self._era_read_action('era_seo_suite.action_seo_audit_finding')
        action.update({
            'name': _('Priority Findings'),
            'domain': [
                ('is_resolved', '=', False),
                ('severity', 'in', ['critical', 'warning']),
            ],
        })
        return action

    def action_open_audit_findings(self):
        """Open ALL unresolved audit findings — every severity, including the
        info-level GEO recommendations that the Priority Findings view filters
        out. This is the dashboard's full Findings list."""
        self.ensure_one()
        action = self._era_read_action('era_seo_suite.action_seo_audit_finding')
        action.update({
            'name': _('Audit Findings'),
            'domain': [('is_resolved', '=', False)],
        })
        return action

    def action_open_ai_review_queue(self):
        """Open AI suggestions that are ready for review."""
        self.ensure_one()
        action = self._era_read_action('era_seo_suite.action_seo_audit_finding')
        action.update({
            'name': _('AI Review Queue'),
            'domain': [
                ('is_resolved', '=', False),
                ('ai_status', '=', 'suggested'),
            ],
        })
        return action

    def action_open_pages_missing_seo(self):
        """Open website pages still missing an SEO title."""
        self.ensure_one()
        action = self._era_read_action('era_seo_suite.action_era_seo_pages')
        action.update({
            'name': _('Pages Missing SEO Title'),
            'domain': [
                '&', ('website_published', '=', True),
                '|', ('seo_title', '=', False), ('seo_title', '=', ''),
            ],
        })
        return action

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
        """Queue a fresh SEO audit in the background and open the empty
        run so the user can watch it finish.

        Was synchronous, but large sites blew past Odoo's HTTP timeout.
        Now: create a draft run, flip the era_seo.audit_pending ICP,
        fire the audit cron via _trigger(), and land the user on the
        run form. The form's polling widget reloads when the cron
        clears the flag.
        """
        self.ensure_one()
        run = self.env['era.seo.audit.run'].create({})
        run._queue_audit_run()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Audit Run'),
            'res_model': 'era.seo.audit.run',
            'res_id': run.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_run_geo_content_analysis(self):
        """Queue a fresh audit, then open the GEO content recommendations.

        Runs the audit in the background (re-evaluating every page), and lands
        the user directly on the GEO findings — the content-quality
        recommendations (answer summary, FAQ coverage, factual density,
        heading structure, AI-crawler access, /llms.txt). These are info-level
        findings, so this view surfaces them on purpose instead of leaving them
        behind the critical/warning Priority Findings filter. The list reflects
        the latest completed scan and refreshes when the queued run finishes.
        Generic: works on any database with this module installed.
        """
        self.ensure_one()
        self.env['era.seo.audit.run'].create({})._queue_audit_run()
        action = self._era_read_action('era_seo_suite.action_seo_audit_finding')
        action.update({
            'name': _('GEO Content Recommendations'),
            'domain': [
                ('is_resolved', '=', False),
                ('check_code', '=like', 'geo%'),
            ],
        })
        return action
