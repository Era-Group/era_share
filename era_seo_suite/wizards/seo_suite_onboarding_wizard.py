"""ERA SEO Suite — Onboarding Wizard.

Walks a fresh admin through every setting the suite needs to be useful,
in dependency order:

    1. Welcome
    2. Organization identity   (logo, OG image, twitter handle, verifies)
    3. Social profiles         (sameAs[] in Organization JSON-LD)
    4. Schema engine           (master JSON-LD switch)
    5. AI Auto-Fix             (enable + agent picker, gated on `ai` install)
    6. GEO — /llms.txt         (publish toggle + site summary)
    7. GSC                     (OAuth client; OPTIONAL — easy to skip)
    8. Done                    (Run First Audit / Open the Hub)

State is stored on a TransientModel for the form session. Each Next
press persists the current step's fields to ``ir.config_parameter``
right away — so closing mid-wizard and reopening picks up exactly
where the user left off, since the next launch pre-fills from ICP.
"""
from odoo import _, api, fields, models


_TRUE = ('True', '1', 'true', 'yes', 'on')

_STEPS = [
    'welcome',
    'org',
    'business',   # Business profile — drives the per-site SEO agent prompt.
    'social',
    'schema',
    'robots',
    'ai',
    'geo',
    'gsc',
    'bulk_ai',
    'blog',
    'done',
]


# (wizard_field, icp_key, kind, default)
# kinds: 'bool' | 'int' | 'char'
_ICP_MAP = [
    # Organization
    ('org_name',           'era_seo.organization_name',        'char', ''),
    ('org_legal_name',     'era_seo.legal_name',               'char', ''),
    ('org_logo_url',       'era_seo.logo_url',                 'char', ''),
    ('org_og_image_url',   'era_seo.default_og_image_url',     'char', ''),
    ('org_twitter_handle', 'era_seo.twitter_handle',           'char', ''),
    ('org_google_verify',  'era_seo.google_site_verification', 'char', ''),
    ('org_bing_verify',    'era_seo.bing_site_verification',   'char', ''),
    # Business profile — feeds the SEO agent's system_prompt builder.
    ('biz_name_en',        'era_seo.business_name_en',         'char', ''),
    ('biz_name_ar',        'era_seo.business_name_ar',         'char', ''),
    ('biz_sector',         'era_seo.business_sector',          'char', 'other'),
    ('biz_audience',       'era_seo.business_audience',        'char', ''),
    ('biz_region',         'era_seo.business_region',          'char', ''),
    ('biz_voice',          'era_seo.business_voice',           'char', 'friendly'),
    ('biz_keywords',       'era_seo.business_keywords',        'char', ''),
    ('biz_avoid',          'era_seo.business_avoid',           'char', ''),
    # Social
    ('social_facebook',    'era_seo.social_facebook',          'char', ''),
    ('social_twitter',     'era_seo.social_twitter',           'char', ''),
    ('social_linkedin',    'era_seo.social_linkedin',          'char', ''),
    ('social_instagram',   'era_seo.social_instagram',         'char', ''),
    ('social_youtube',     'era_seo.social_youtube',           'char', ''),
    # Schema
    ('schema_enabled',     'era_seo.schema_engine_enabled',    'bool', True),
    # AI
    ('ai_enabled',         'era_seo.ai_enabled',               'bool', False),
    # GEO
    ('llms_enabled',       'era_seo_suite.llms_enabled',       'bool', True),
    ('llms_summary',       'era_seo_suite.site_summary',       'char', ''),
    ('llms_max_items',     'era_seo_suite.llms_max_items',     'int',  100),
    ('llms_include_blog',  'era_seo_suite.llms_include_blog',  'bool', True),
    # GSC
    ('gsc_client_id',      'era_seo_suite.client_id',          'char', ''),
    ('gsc_client_secret',  'era_seo_suite.client_secret',      'char', ''),
    ('gsc_pull_window',    'era_seo_suite.pull_window_days',   'int',  28),
]

# Fields persisted on each Next press, grouped by step.
_STEP_FIELDS = {
    'org': [
        'org_name', 'org_legal_name', 'org_logo_url', 'org_og_image_url',
        'org_twitter_handle', 'org_google_verify', 'org_bing_verify',
    ],
    'business': [
        'biz_name_en', 'biz_name_ar', 'biz_sector', 'biz_audience',
        'biz_region', 'biz_voice', 'biz_keywords', 'biz_avoid',
    ],
    'social': [
        'social_facebook', 'social_twitter', 'social_linkedin',
        'social_instagram', 'social_youtube',
    ],
    'schema': ['schema_enabled'],
    'ai':     ['ai_enabled', 'ai_agent_id'],
    'geo':    ['llms_enabled', 'llms_summary', 'llms_max_items', 'llms_include_blog'],
    'gsc':    ['gsc_client_id', 'gsc_client_secret', 'gsc_pull_window'],
}


class EraSeoOnboardingWizard(models.TransientModel):
    _name = 'era.seo.onboarding.wizard'
    _description = 'ERA SEO Suite — Onboarding Wizard'

    step = fields.Selection(
        [
            ('welcome',  'Welcome'),
            ('org',      '1. Organization'),
            ('business', '2. Business profile'),
            ('social',   '3. Social profiles'),
            ('schema',   '4. Schema engine'),
            ('robots',   '5. Robots & AI Crawlers'),
            ('ai',       '6. AI Auto-Fix'),
            ('geo',      '7. GEO (/llms.txt)'),
            ('gsc',      '8. Google Search Console'),
            ('bulk_ai',  '9. AI Bulk Fill'),
            ('blog',     '10. Blog setup'),
            ('done',     'Done'),
        ],
        default='welcome', required=True,
    )

    # ---- Organization ------------------------------------------------------
    org_name = fields.Char(string='Organization Name',
                           help='Used in Organization JSON-LD and Twitter site tag.')
    org_legal_name = fields.Char(string='Legal Name',
                                 help='Legal entity if different from the brand.')
    org_logo_url = fields.Char(string='Logo URL',
                               help='Absolute URL — renders into Organization JSON-LD.')
    org_og_image_url = fields.Char(string='Default Open Graph Image URL',
                                   help='Fallback OG image when a page has no per-page one.')
    org_twitter_handle = fields.Char(string='Twitter / X Handle',
                                     help='Start with @, e.g. @era.')
    org_google_verify = fields.Char(string='Google Site Verification',
                                    help='content="…" from Google Search Console\'s HTML meta tag.')
    org_bing_verify = fields.Char(string='Bing Site Verification',
                                  help='content="…" from Bing Webmaster Tools\' meta tag.')

    # ---- Business profile --------------------------------------------------
    # Feeds era.seo.suite.hub._build_seo_agent_prompt(). Re-saving the step
    # rewrites the agent's system_prompt — so a fresh install gets a generic
    # SEO Fixer, and the wizard turns it into a brand-aware one.
    biz_name_en = fields.Char(
        string='Business Name (English)',
        help='How the brand is written in English copy. Used as the brand '
             'suffix candidate in seo_title proposals.')
    biz_name_ar = fields.Char(
        string='Business Name (Arabic)',
        help='How the brand is written in Arabic copy. Used as the brand '
             'suffix on Arabic pages.')
    biz_sector = fields.Selection(
        [
            ('services',    'Professional services / consulting'),
            ('ecommerce',   'E-commerce / online store'),
            ('hospitality', 'Hospitality / restaurants / hotels'),
            ('healthcare',  'Healthcare / clinics'),
            ('realestate',  'Real estate / property'),
            ('education',   'Education / training'),
            ('industrial',  'Industrial / manufacturing / B2B'),
            ('tech',        'Software / SaaS / technology'),
            ('other',       'Other / general'),
        ],
        default='other',
        string='Industry',
        help='Picks the right worked examples and tone reference for the '
             'SEO agent. Pick "Other" if nothing fits — the agent still '
             'gets a generic but on-brand prompt.')
    biz_audience = fields.Char(
        string='Primary Audience',
        help='One short line, e.g. "Saudi SMEs", "luxury travellers in the '
             'Gulf", "freshman computer science students". Inlined into the '
             'agent prompt as "targeting <audience>".')
    biz_region = fields.Char(
        string='Primary Market / Region',
        help='Country, city or region your pages mainly target — e.g. '
             '"Saudi Arabia (KSA)", "Riyadh", "GCC". The agent surfaces '
             'local keywords on pages that fit this market.')
    biz_voice = fields.Selection(
        [
            ('formal',     'Formal & authoritative'),
            ('friendly',   'Friendly & conversational'),
            ('technical',  'Technical & precise'),
            ('persuasive', 'Persuasive & benefit-led'),
        ],
        default='friendly',
        string='Brand Voice',
        help='Tells the agent which tone to mimic in proposed titles and '
             'meta descriptions.')
    biz_keywords = fields.Char(
        string='Brand Keywords (comma-separated)',
        help='3-8 keywords your customers actually search for. The agent '
             'weaves them in only when they fit a page — never forced.')
    biz_avoid = fields.Char(
        string='Words to Avoid (comma-separated)',
        help='Words, claims or product names the agent must never use — '
             'e.g. "best", "cheapest", competitor names, off-strategy '
             'product lines.')
    biz_prompt_preview = fields.Text(
        string='Generated System Prompt (preview)',
        compute='_compute_biz_prompt_preview', readonly=True,
        help='Live preview of the system prompt that will be saved to the '
             'SEO Fixer agent when you click Next.')

    # ---- Social ------------------------------------------------------------
    social_facebook = fields.Char(string='Facebook')
    social_twitter = fields.Char(string='Twitter / X profile URL')
    social_linkedin = fields.Char(string='LinkedIn')
    social_instagram = fields.Char(string='Instagram')
    social_youtube = fields.Char(string='YouTube')

    # ---- Schema ------------------------------------------------------------
    schema_enabled = fields.Boolean(string='Enable Schema Engine', default=True)

    # ---- Robots & AI Crawlers ---------------------------------------------
    robots_url = fields.Char(string='robots.txt URL',
                             compute='_compute_robots_state', readonly=True)
    robots_preview = fields.Text(string='robots.txt preview (live)',
                                 compute='_compute_robots_state', readonly=True)
    robots_count_total = fields.Integer(compute='_compute_robots_state')
    robots_count_allowed = fields.Integer(compute='_compute_robots_state')
    robots_count_blocked = fields.Integer(compute='_compute_robots_state')
    robots_count_training_allowed = fields.Integer(compute='_compute_robots_state')
    robots_count_search_allowed = fields.Integer(compute='_compute_robots_state')

    # ---- AI ----------------------------------------------------------------
    ai_enabled = fields.Boolean(string='Enable AI Auto-Fix')
    ai_agent_id = fields.Many2one('ai.agent', string='AI Agent')
    ai_module_installed = fields.Boolean(compute='_compute_ai_module_installed')
    ai_agent_count = fields.Integer(compute='_compute_ai_agent_count')

    # ---- GEO ---------------------------------------------------------------
    llms_enabled = fields.Boolean(string='Publish /llms.txt', default=True)
    llms_summary = fields.Char(string='Site Summary (one line)')
    llms_max_items = fields.Integer(string='Max Items in /llms.txt', default=100)
    llms_include_blog = fields.Boolean(string='Include Blog', default=True)

    # ---- GSC ---------------------------------------------------------------
    gsc_client_id = fields.Char(string='OAuth Client ID')
    gsc_client_secret = fields.Char(string='OAuth Client Secret')
    gsc_pull_window = fields.Integer(string='Pull Window (days)', default=28)
    gsc_redirect_uri = fields.Char(string='Authorized Redirect URI',
                                   compute='_compute_gsc_redirect_uri', readonly=True)
    gsc_connected_count = fields.Integer(compute='_compute_gsc_connected_count')
    gsc_can_connect = fields.Boolean(compute='_compute_gsc_can_connect')

    # ---- AI Bulk Fill ------------------------------------------------------
    bulk_ai_opt_in = fields.Boolean(
        string='Run AI Bulk Fill now',
        help='Queue a background cron to walk every website page and blog post '
             'and let the AI agent fill the empty SEO fields. Safe to leave '
             'on — runs in batches of 10 every 2 minutes, stops automatically '
             'when done, and re-running is idempotent.')
    bulk_ai_already_running = fields.Boolean(compute='_compute_bulk_ai_already_running')
    bulk_ai_pending_count = fields.Integer(compute='_compute_bulk_ai_pending_count',
                                           string='Pages / posts pending fill')

    # ---- Blog setup --------------------------------------------------------
    blog_taxonomy_opt_in = fields.Boolean(
        string='Auto-classify blog posts (AI)',
        help='Have the cron, while it fills SEO fields, also ask the AI to '
             'assign every blog post to a category (and series, when the '
             'post is clearly part of a multi-part arc). Existing categories '
             'and series are reused before new ones are created.')
    blog_post_count = fields.Integer(compute='_compute_blog_counts')
    blog_uncategorized_count = fields.Integer(compute='_compute_blog_counts')
    blog_category_count = fields.Integer(compute='_compute_blog_counts')
    blog_series_count = fields.Integer(compute='_compute_blog_counts')

    # =======================================================================
    # Computes
    # =======================================================================

    def _compute_ai_module_installed(self):
        for rec in self:
            rec.ai_module_installed = 'ai.agent' in self.env

    def _compute_ai_agent_count(self):
        Agent = self.env.get('ai.agent')
        n = Agent.sudo().search_count([]) if Agent is not None else 0
        for rec in self:
            rec.ai_agent_count = n

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

    def _compute_bulk_ai_already_running(self):
        ICP = self.env['ir.config_parameter'].sudo()
        flag = ICP.get_param('era_seo.bulk_ai_fill_active') in _TRUE
        for rec in self:
            rec.bulk_ai_already_running = flag

    def _compute_bulk_ai_pending_count(self):
        Hub = self.env['era.seo.suite.hub']
        count = 0
        for model_name in Hub._BULK_AI_MODELS:
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
            rec.bulk_ai_pending_count = count

    def _compute_gsc_connected_count(self):
        Account = self.env.get('era.gsc.account')
        n = (Account.sudo().search_count([('state', '=', 'connected')])
             if Account is not None else 0)
        for rec in self:
            rec.gsc_connected_count = n

    def _compute_gsc_can_connect(self):
        for rec in self:
            rec.gsc_can_connect = bool(
                (rec.gsc_client_id or '').strip()
                and (rec.gsc_client_secret or '').strip()
            )

    @api.depends('biz_name_en', 'biz_name_ar', 'biz_sector', 'biz_audience',
                 'biz_region', 'biz_voice', 'biz_keywords', 'biz_avoid')
    def _compute_biz_prompt_preview(self):
        """Render the prompt from the wizard's draft fields, without writing
        anything to ICP. Hub's builder accepts an explicit profile dict
        precisely so previews stay side-effect-free.
        """
        Hub = self.env.get('era.seo.suite.hub')
        if Hub is None:
            for rec in self:
                rec.biz_prompt_preview = ''
            return
        for rec in self:
            profile = {
                'name_en':  rec.biz_name_en or '',
                'name_ar':  rec.biz_name_ar or '',
                'sector':   rec.biz_sector or 'other',
                'audience': rec.biz_audience or '',
                'region':   rec.biz_region or '',
                'voice':    rec.biz_voice or 'friendly',
                'keywords': rec.biz_keywords or '',
                'avoid':    rec.biz_avoid or '',
            }
            preview = Hub.sudo()._build_seo_agent_prompt(profile=profile)
            rec.biz_prompt_preview = (
                preview or
                _('Fill in at least the Business Name to see the '
                  'generated SEO Fixer prompt.'))

    def _compute_robots_state(self):
        Crawler = self.env.get('era.geo.ai.crawler')
        ICP = self.env['ir.config_parameter'].sudo()
        base = (ICP.get_param('web.base.url') or '').rstrip('/')
        robots_url = (base + '/robots.txt') if base else '/robots.txt'

        if Crawler is None:
            for rec in self:
                rec.robots_url = robots_url
                rec.robots_preview = _('Crawler model not installed yet.')
                rec.robots_count_total = 0
                rec.robots_count_allowed = 0
                rec.robots_count_blocked = 0
                rec.robots_count_training_allowed = 0
                rec.robots_count_search_allowed = 0
            return

        crawlers = Crawler.sudo().search([])
        total = len(crawlers)
        allowed = sum(1 for c in crawlers if c.allowed)
        blocked = total - allowed
        # Per-purpose visibility into what's currently leaking.
        training_allowed = sum(
            1 for c in crawlers if c.allowed and c.purpose in ('training', 'both'))
        search_allowed = sum(
            1 for c in crawlers if c.allowed and c.purpose in ('search', 'both'))

        # Live preview: render the same block the controller emits, so the
        # admin sees what's actually being served right now.
        try:
            block = Crawler.sudo()._robots_block() or ''
        except Exception:  # noqa: BLE001
            block = _('(could not render the AI-crawler block — install or '
                      'enable the GEO module first)')
        preview = ('User-agent: *\n'
                   'Allow: /\n'
                   '\n'
                   '# ↓ ERA SEO Suite — AI crawler directives ↓\n'
                   + (block.strip() if block else '(no AI crawler rules yet)'))

        for rec in self:
            rec.robots_url = robots_url
            rec.robots_preview = preview
            rec.robots_count_total = total
            rec.robots_count_allowed = allowed
            rec.robots_count_blocked = blocked
            rec.robots_count_training_allowed = training_allowed
            rec.robots_count_search_allowed = search_allowed

    # ---- Bulk crawler actions (called from the Robots step) ---------------

    def action_robots_allow_all(self):
        self.ensure_one()
        self.env['era.geo.ai.crawler'].sudo().search([]).write({'allowed': True})
        return self._go_step('robots')

    def action_robots_block_training(self):
        self.ensure_one()
        crawlers = self.env['era.geo.ai.crawler'].sudo().search(
            [('purpose', 'in', ('training', 'both'))])
        crawlers.write({'allowed': False})
        return self._go_step('robots')

    def action_robots_block_search(self):
        self.ensure_one()
        crawlers = self.env['era.geo.ai.crawler'].sudo().search(
            [('purpose', 'in', ('search', 'both'))])
        crawlers.write({'allowed': False})
        return self._go_step('robots')

    def action_robots_manage(self):
        """Open the full AI Crawlers list for fine-grained control."""
        self.ensure_one()
        return self.env['ir.actions.actions']._for_xml_id(
            'era_seo_suite.action_era_geo_ai_crawler')

    def _compute_gsc_redirect_uri(self):
        base = (self.env['ir.config_parameter'].sudo()
                .get_param('web.base.url') or '').rstrip('/')
        uri = (base + '/era_gsc/oauth/callback') if base \
            else '/era_gsc/oauth/callback'
        for rec in self:
            rec.gsc_redirect_uri = uri

    # =======================================================================
    # ICP round-trip
    # =======================================================================

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Pre-fill the form from current ICP, so re-launching the wizard
        # picks up wherever the admin left off.
        for rec in records:
            rec._load_from_icp()
        return records

    def _load_from_icp(self):
        ICP = self.env['ir.config_parameter'].sudo()
        vals = {}
        for fname, key, kind, default in _ICP_MAP:
            raw = ICP.get_param(key)
            if kind == 'bool':
                vals[fname] = (raw in _TRUE) if raw not in ('', None) else default
            elif kind == 'int':
                try:
                    vals[fname] = int(raw) if raw not in ('', None) else default
                except (TypeError, ValueError):
                    vals[fname] = default
            else:
                vals[fname] = raw or default
        # AI agent id is a Many2one — round-trip via the stored ICP id.
        agent_raw = ICP.get_param('era_seo.ai_agent_id')
        if agent_raw and 'ai.agent' in self.env:
            try:
                agent = self.env['ai.agent'].sudo().browse(int(agent_raw))
                if agent.exists():
                    vals['ai_agent_id'] = agent.id
            except (TypeError, ValueError):
                pass
        self.write(vals)

    def _save_current_step(self):
        """Persist whichever fields this step owns to ir.config_parameter."""
        # Bulk-AI step has no ICP map entries — it just triggers the hub's
        # start helper when the user opts in.
        if self.step == 'bulk_ai':
            if self.bulk_ai_opt_in:
                self.env['era.seo.suite.hub'].sudo().start_bulk_ai_fill()
            return
        # Business profile step: persist via the generic _ICP_MAP path
        # below, then rebuild the SEO agent's system_prompt so it picks
        # up the new profile immediately — no Odoo restart needed.
        if self.step == 'business':
            self._persist_step_via_icp_map()
            self.env['era.seo.suite.hub'].sudo().rebuild_seo_agent_prompt()
            return
        # Blog setup step flips a separate flag the same cron reads when
        # processing blog.post records — categories + series are assigned
        # alongside the SEO fill in the same tick.
        if self.step == 'blog':
            ICP = self.env['ir.config_parameter'].sudo()
            ICP.set_param('era_seo.blog_taxonomy_active',
                          'True' if self.blog_taxonomy_opt_in else 'False')
            # If the user toggled taxonomy on but never opted into the bulk
            # fill, we still need the cron to run — flip its gate too so
            # it actually wakes up.
            if self.blog_taxonomy_opt_in and \
                    ICP.get_param('era_seo.bulk_ai_fill_active') not in _TRUE:
                self.env['era.seo.suite.hub'].sudo().start_bulk_ai_fill()
            return
        self._persist_step_via_icp_map()

    def _persist_step_via_icp_map(self):
        """Write the current step's _STEP_FIELDS entries to ir.config_parameter,
        using each field's _ICP_MAP kind (bool / int / char) for serialisation.

        Pulled out so the Business step can call it explicitly and *then*
        rebuild the SEO agent prompt — the generic path does the persist,
        the rebuild happens after.
        """
        names = _STEP_FIELDS.get(self.step, [])
        if not names:
            return
        ICP = self.env['ir.config_parameter'].sudo()
        icp_lookup = {f: (k, t) for f, k, t, _d in _ICP_MAP}
        for fname in names:
            if fname == 'ai_agent_id':
                ICP.set_param(
                    'era_seo.ai_agent_id',
                    str(self.ai_agent_id.id) if self.ai_agent_id else '',
                )
                continue
            key, kind = icp_lookup[fname]
            val = self[fname]
            if kind == 'bool':
                ICP.set_param(key, 'True' if val else 'False')
            elif kind == 'int':
                ICP.set_param(key, str(int(val or 0)))
            else:
                ICP.set_param(key, val or '')

    # =======================================================================
    # Navigation actions
    # =======================================================================

    def _go_step(self, target):
        self.step = target
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    def action_next(self):
        self.ensure_one()
        self._save_current_step()
        idx = _STEPS.index(self.step)
        return self._go_step(_STEPS[min(idx + 1, len(_STEPS) - 1)])

    def action_prev(self):
        self.ensure_one()
        idx = _STEPS.index(self.step)
        return self._go_step(_STEPS[max(idx - 1, 0)])

    def action_skip(self):
        """Advance without persisting the current step (use for optional steps)."""
        self.ensure_one()
        idx = _STEPS.index(self.step)
        return self._go_step(_STEPS[min(idx + 1, len(_STEPS) - 1)])

    def action_connect_gsc(self):
        """Persist the OAuth creds the user just typed, then kick off the
        GSC authorize flow on a (created if needed) era.gsc.account record.

        Returns the same act_url action GSC's account form returns — the
        browser leaves Odoo for Google's consent screen and the OAuth
        callback writes the tokens back. The user re-opens the wizard
        afterwards to continue.
        """
        self.ensure_one()
        # Persist whatever's in the GSC form first, so action_authorize
        # finds the client_id when it looks it up via ICP.
        self._save_current_step()
        Account = self.env['era.gsc.account'].sudo()
        account = Account.search([('active', '=', True)], limit=1)
        if not account:
            account = Account.create({'name': _('Default GSC connection')})
        return account.action_authorize()

    def action_open_ai_config(self):
        """Jump to the AI app so the admin can configure a provider/key."""
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('ai.ai_agent_action')
        action['target'] = 'current'
        return action

    def action_open_hub(self):
        """Land on the suite hub — used by the Done step's primary button."""
        self.ensure_one()
        Hub = self.env['era.seo.suite.hub'].sudo()
        rec = Hub.search([], limit=1) or Hub.create({'name': 'ERA SEO Suite'})
        return {
            'type': 'ir.actions.act_window',
            'name': _('ERA SEO Suite'),
            'res_model': 'era.seo.suite.hub',
            'res_id': rec.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_run_first_audit(self):
        """Launch the audit wizard from the Done step."""
        self.ensure_one()
        return self.env['ir.actions.actions']._for_xml_id(
            'era_seo_suite.action_seo_audit_wizard'
        )

    # =======================================================================
    # Entry point — called by the menuitem
    # =======================================================================

    @api.model
    def action_open(self):
        """Create a fresh wizard pre-filled from ICP and open it."""
        wiz = self.create({})
        return {
            'type': 'ir.actions.act_window',
            'name': _('ERA SEO Suite — Setup'),
            'res_model': self._name,
            'res_id': wiz.id,
            'view_mode': 'form',
            'target': 'new',
        }
