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
        'setting_llms_enabled':     ('era_seo_suite.llms_enabled',          'bool', True),
        'setting_llms_summary':     ('era_seo_suite.site_summary',          'char', ''),
        'setting_llms_max_items':   ('era_seo_suite.llms_max_items',        'int', 100),
        'setting_llms_include_blog':('era_seo_suite.llms_include_blog',     'bool', True),
        # ---------- GSC (era_gsc) ----------
        'setting_gsc_client_id':    ('era_seo_suite.client_id',             'char', ''),
        'setting_gsc_client_secret':('era_seo_suite.client_secret',         'char', ''),
        'setting_gsc_pull_window':  ('era_seo_suite.pull_window_days',      'int', 28),
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
            # Drop the now-stale cache so the next read goes through
            # `_compute_settings` and reflects the values just written.
            self.invalidate_recordset(list(setting_vals))
        return super().write(vals) if vals else True

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

    @api.model
    def cron_bulk_ai_fill(self):
        """Cron entry point — process one batch of pending records.

        Cron stays scheduled but is a no-op until
        ``era_seo.bulk_ai_fill_active`` is True. The cron record itself can
        be active=True permanently; the gate is the ICP flag.
        """
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
                try:
                    rec.action_ai_fill_seo()
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
                total_processed += 1

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
            cat = Category.create({'name': cat_name})
        vals = {'era_category_id': cat.id}
        if pick['series']:
            ser_name = pick['series'].strip()
            ser = Series.search([('name', '=ilike', ser_name)], limit=1)
            if not ser:
                ser = Series.create({'name': ser_name})
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
            findings.action_ai_suggest_and_apply()
        except Exception:  # noqa: BLE001
            _logger.exception(
                'weekly_audit_and_fix: AI suggest+apply failed on %d findings',
                len(findings))

    # ------------------------------------------------------------------------
    # Auto-publish: trend-aware blog article every N days
    # ------------------------------------------------------------------------

    @api.model
    def cron_generate_blog_article(self):
        """Generate a fresh, trend-aware blog post from the AI agent.

        Cron entry runs every 3 days. Gated by
        `era_seo.article_generator_active` (default False — opt-in).

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
        if _icp_get(self.env, 'era_seo.article_generator_active') not in _TRUE:
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
        try:
            article = client.propose_article(
                business_context, past_titles, existing_categories)
        except (AIUnavailable, ValueError) as exc:
            _logger.warning('cron_generate_blog_article: skipped — %s', exc)
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
                cat = Category.sudo().create({'name': article['category']})
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
            'is_published': False,  # Stays in draft for human review.
        }
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
        if 'era_category_id' in BlogPost._fields and category:
            post_vals['era_category_id'] = category.id

        post = BlogPost.sudo().create(post_vals)

        # Hero image — call the hook, attach if it returned bytes.
        try:
            image_bytes = self._generate_article_image(article['image_prompt'])
        except Exception:  # noqa: BLE001
            _logger.exception(
                'cron_generate_blog_article: image hook raised — skipping image')
            image_bytes = None
        if image_bytes:
            try:
                import base64
                vals = {}
                # blog.post's stock cover field is `cover_properties` JSON
                # in modern versions; era_seo_blog may add `era_cover_image`
                # (Binary). Write whichever exists.
                if 'era_cover_image' in BlogPost._fields:
                    vals['era_cover_image'] = base64.b64encode(image_bytes)
                if vals:
                    post.write(vals)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    'cron_generate_blog_article: failed to attach generated image')

        _logger.info(
            'cron_generate_blog_article: created blog.post#%d "%s" '
            '(trend: %s, confidence %.2f)',
            post.id, article['title'][:80], article['trend_signal'][:80],
            article['confidence'])
        return post.id

    def _generate_article_image(self, prompt):
        """Hook — return raw image bytes (PNG/JPEG) for the article's hero,
        or None to skip.

        Default returns None because Odoo's stock AI app doesn't expose
        image generation. Wire up DALL-E / Stable Diffusion / etc. by
        overriding this method in a small custom module that calls the
        relevant provider and returns the bytes.
        """
        return None

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
