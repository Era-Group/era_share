# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class CrmAiAgent(models.Model):
    """Central registry: one record per AI agent.

    Every satellite module (Dead-Lead, Enrichment, ...) registers itself here
    on install via :meth:`_get_or_create_agent`, keyed by a stable ``tech_name``.
    The record holds the agent's identity, its default LLM model, activation
    state, and a rolling monthly cost counter used by the hard cost cap (Rule 14).
    """

    _name = "crm.ai.agent"
    _description = "CRM AI Agent Registry"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    tech_name = fields.Char(
        string="Technical Name",
        required=True,
        index=True,
        copy=False,
        help="Stable unique key satellite modules use to find or create their "
             "agent record on install. Underscores only, no spaces.",
    )
    icon = fields.Binary(string="Icon", attachment=True)

    # --------------------------------------------------------------
    # Transport: which AI path this agent's LLM calls take.
    #   'api' — Odoo native AI (OpenAI/Google) over HTTP. Priced via the rate
    #           card; governed by the DOLLAR cost cap (Rule 14).
    #   'cli' — Claude via the era_ai_accounts local CLI proxy (subscription).
    #           No per-token dollar cost; governed by the monthly TOKEN limit.
    # In BOTH cases the call MUST go through crm.ai.agent.mixin._call_llm, which
    # stamps CTX_AGENT so the AI Compliance Guard runs full enforcement (consent +
    # PII redaction) BEFORE the prompt reaches the provider/CLI.
    # --------------------------------------------------------------
    transport = fields.Selection(
        selection=[
            ("api", "Native API (OpenAI / Google)"),
            ("cli", "Claude CLI (subscription)"),
        ],
        string="Transport",
        default="api",
        required=True,
        help="How this agent calls the LLM. 'Native API' is priced per token "
             "(dollar cost cap). 'Claude CLI' runs through the era_ai_accounts "
             "subscription proxy — no dollar cost, governed by the monthly token "
             "limit; tokens are estimated.",
    )
    cli_account_id = fields.Many2one(
        comodel_name="era.ai.account",
        string="Claude Account",
        ondelete="set null",
        help="The era_ai_accounts provider account used when Transport is "
             "'Claude CLI'. Should be a local-CLI-proxy (Anthropic) account.",
    )
    cli_model_code = fields.Char(
        string="CLI Model",
        default="opus",
        help="Claude CLI model alias (e.g. 'opus', 'sonnet', 'haiku') or a full "
             "model id. Aliases resolve to the latest model of that tier.",
    )

    # Model SELECTION lives here (not in the rate card). Each agent declares the
    # native model code(s) it uses; the provider is derived from the code. This
    # is what makes "this agent uses gpt-4o for Arabic drafting" explicit and
    # manager-configurable — and fixes the old catalog-ordering default.
    # (Used for the 'api' transport; the 'cli' transport uses cli_model_code.)
    model_code = fields.Char(
        string="Model",
        help="Native model code this agent uses by default (e.g. "
             "'gpt-4.1-mini', 'gemini-2.5-flash'). Must be supported by Odoo "
             "native AI and priced in the rate card (CRM AI → Models).",
    )
    model_code_advanced = fields.Char(
        string="Advanced Model",
        help="Optional native model code for high-sensitivity calls "
             "(sensitivity='high'). Falls back to Model when empty.",
    )

    active = fields.Boolean(default=True)
    state = fields.Selection(
        selection=[
            ("enabled", "Enabled"),
            ("paused", "Paused"),
            ("capped", "Capped"),
        ],
        string="State",
        default="enabled",
        required=True,
        copy=False,
        help="Enabled: running. Paused: switched off by a user. "
             "Capped: auto-paused because the monthly cost cap was hit (Rule 14).",
    )
    last_run = fields.Datetime(string="Last Run", readonly=True, copy=False)

    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id.id,
    )
    monthly_cost = fields.Monetary(
        string="Monthly Cost",
        compute="_compute_monthly_cost",
        currency_field="currency_id",
        help="Total AI spend recorded for this agent in the current calendar "
             "month, summed from crm.ai.usage. Meaningful for the 'Native API' "
             "transport; the 'Claude CLI' transport has no dollar cost — see "
             "Monthly Tokens.",
    )
    monthly_tokens = fields.Integer(
        string="Monthly Tokens",
        compute="_compute_monthly_tokens",
        help="Total (estimated) tokens recorded for this agent in the current "
             "calendar month. This is the consumption figure governed by the "
             "monthly token limit on the 'Claude CLI' (subscription) transport — "
             "where there is no dollar cost, this is NOT zero/free.",
    )

    _sql_constraints = [
        (
            "tech_name_uniq",
            "unique(tech_name)",
            "The technical name of an AI agent must be unique.",
        ),
    ]

    def _compute_monthly_cost(self):
        """Sum crm.ai.usage cost for the current month, per agent.

        Guarded against crm.ai.usage not existing yet (it arrives in task 0.5)
        so the base module stays loadable while it is built up task by task.
        """
        has_usage = "crm.ai.usage" in self.env
        month_start = fields.Date.context_today(self).replace(day=1)
        month_start_dt = fields.Datetime.to_datetime(month_start)
        for agent in self:
            cost = 0.0
            # Skip transient/NewId records (id is not a real int) and only
            # aggregate once the usage model is available.
            if has_usage and isinstance(agent.id, int):
                usage = self.env["crm.ai.usage"].search([
                    ("agent_id", "=", agent.id),
                    ("create_date", ">=", month_start_dt),
                ])
                cost = sum(usage.mapped("cost"))
            agent.monthly_cost = cost

    def _compute_monthly_tokens(self):
        """Sum crm.ai.usage total_tokens for the current month, per agent.

        Mirrors _compute_monthly_cost; this is the consumption figure the CLI
        token limit governs. Guarded the same way (model may not exist yet /
        transient records)."""
        has_usage = "crm.ai.usage" in self.env
        month_start = fields.Date.context_today(self).replace(day=1)
        month_start_dt = fields.Datetime.to_datetime(month_start)
        for agent in self:
            tokens = 0
            if has_usage and isinstance(agent.id, int):
                usage = self.env["crm.ai.usage"].search([
                    ("agent_id", "=", agent.id),
                    ("create_date", ">=", month_start_dt),
                ])
                tokens = sum(usage.mapped("total_tokens"))
            agent.monthly_tokens = tokens

    @api.constrains("transport", "cli_account_id")
    def _check_cli_account(self):
        for agent in self:
            if agent.transport == "cli" and not agent.cli_account_id:
                raise ValidationError(_(
                    "AI agent '%(name)s' uses the Claude CLI transport but has no "
                    "Claude Account set. Pick an era_ai_accounts account.",
                    name=agent.name))

    def action_pause(self):
        """Manually switch the agent off (manager action)."""
        return self.write({"state": "paused"})

    def action_enable(self):
        """Re-enable a paused or capped agent (manager action)."""
        return self.write({"state": "enabled"})

    # ------------------------------------------------------------------
    # Controlled writers for the (sudo-free) mixin — approved sudo elevation,
    # each strictly field-scoped so AI users stay read-only on this model.
    # ------------------------------------------------------------------
    def _record_run(self):
        """Stamp last_run on each LLM call."""
        self.sudo().write({"last_run": fields.Datetime.now()})

    def _mark_state(self, state):
        """Set the agent state (used by the cap auto-pause)."""
        self.sudo().write({"state": state})

    @api.model
    def _get_or_create_agent(self, tech_name, default_vals=None):
        """Return the agent for ``tech_name``, creating it if missing.

        Satellite modules call this (e.g. in a post-init hook) so they can
        create-or-find their agent record on install without duplicating it.
        Archived records are matched too, to avoid creating a second copy.

        Approved sudo elevation: lookup/first-use creation run as superuser so
        read-only AI users can resolve their agent; the result is rebound to the
        caller's environment so the mixin keeps operating under the user.
        """
        if not tech_name:
            raise ValueError("tech_name is required to look up a crm.ai.agent.")
        agent = self.sudo().with_context(active_test=False).search(
            [("tech_name", "=", tech_name)], limit=1
        )
        if not agent:
            vals = dict(default_vals or {})
            vals.setdefault("tech_name", tech_name)
            vals.setdefault("name", tech_name)
            agent = self.sudo().create(vals)
        return agent.with_env(self.env)

    # ------------------------------------------------------------------
    # Model selection (provider derived from the code)
    # ------------------------------------------------------------------
    @api.model
    def _provider_for_code(self, code):
        """Map a native model code to its Odoo-native provider."""
        c = (code or "").lower()
        if c.startswith("gemini") or c.startswith("google"):
            return "google"
        return "openai"  # gpt-*, o*, chatgpt-*, etc.

    def _model_for_sensitivity(self, sensitivity):
        """Return (provider, code) for this agent at the requested sensitivity.

        'high' uses ``model_code_advanced`` when set, else ``model_code``.
        Raises if the agent has no model configured (no silent default).
        """
        self.ensure_one()
        code = self.model_code
        if sensitivity == "high" and self.model_code_advanced:
            code = self.model_code_advanced
        if not code:
            raise UserError(_(
                "AI agent '%(name)s' has no model configured. Set its Model "
                "(and optionally Advanced Model) on the agent.", name=self.name))
        return self._provider_for_code(code), code

    # ------------------------------------------------------------------
    # Cron execution identity (Rule 09) — the single suite-wide resolver
    # ------------------------------------------------------------------
    @api.model
    def _get_cron_run_user(self):
        """Return the res.users every agent cron in the suite should run as.

        Two modes, from ``era_crm_ai_agents.cron_run_mode``:
          * ``user`` (default): the internal user picked in Settings
            (``era_crm_ai_agents.cron_run_user_id``), seeded to the
            least-privilege ``CRM AI Automation`` account. Runs under real
            ACLs/record rules (Rule 09).
          * ``odoobot``: ``base.user_root`` (superuser; bypasses ACLs/record
            rules). Opt-in, with a visible Settings warning.

        Fail-safe: a missing/invalid/portal/inactive configured user falls back
        to the seeded automation account, then to the current user — NEVER
        silently to root. Reading config is the Base's approved sudo elevation
        #1 (config read only); resolving the user is a plain read.

        Agent crons apply it with ``model.with_user(...)``. Odoo forces
        ``su=True`` only for SUPERUSER_ID, so ``with_user(automation_user)``
        keeps ACLs in force while ``with_user(base.user_root)`` elevates — no
        manual ``sudo()`` needed.
        """
        icp = self.env["ir.config_parameter"].sudo()
        mode = (icp.get_param("era_crm_ai_agents.cron_run_mode") or "user").strip()

        if mode == "odoobot":
            return self.env.ref("base.user_root")

        # 'user' mode (and any unknown value -> safe default, never root).
        Users = self.env["res.users"].sudo()
        user = Users.browse()
        raw = icp.get_param("era_crm_ai_agents.cron_run_user_id")
        if raw:
            try:
                user = Users.browse(int(raw)).exists()
            except (TypeError, ValueError):
                user = Users.browse()
        # Reject anything that is not a usable internal account.
        if not user or not user.active or user.share:
            user = self.env.ref(
                "era_crm_ai_agents_base.user_crm_ai_automation",
                raise_if_not_found=False)
        if not user or not user.active or user.share:
            # Last resort: the calling user (still never the superuser).
            user = self.env.user
        return user
