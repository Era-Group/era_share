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
    'social',
    'schema',
    'ai',
    'geo',
    'gsc',
    'bulk_ai',
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
            ('welcome', 'Welcome'),
            ('org',     '1. Organization'),
            ('social',  '2. Social profiles'),
            ('schema',  '3. Schema engine'),
            ('ai',      '4. AI Auto-Fix'),
            ('geo',     '5. GEO (/llms.txt)'),
            ('gsc',     '6. Google Search Console'),
            ('bulk_ai', '7. AI Bulk Fill'),
            ('done',    'Done'),
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

    # ---- Social ------------------------------------------------------------
    social_facebook = fields.Char(string='Facebook')
    social_twitter = fields.Char(string='Twitter / X profile URL')
    social_linkedin = fields.Char(string='LinkedIn')
    social_instagram = fields.Char(string='Instagram')
    social_youtube = fields.Char(string='YouTube')

    # ---- Schema ------------------------------------------------------------
    schema_enabled = fields.Boolean(string='Enable Schema Engine', default=True)

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
        names = _STEP_FIELDS.get(self.step, [])
        if not names:
            return
        ICP = self.env['ir.config_parameter'].sudo()
        # Build a quick lookup for the _ICP_MAP entries we care about.
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
