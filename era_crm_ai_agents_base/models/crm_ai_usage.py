# -*- coding: utf-8 -*-
"""Per-call cost tracking, monthly aggregation, and the hard cost cap (Rule 14).

Two parallel consumption controls, same shape:

* ``is_over_cap`` — DOLLAR cost cap for the priced native API path (OpenAI/Google),
  where the rate card gives a per-token price.
* ``is_over_token_limit`` — estimated-TOKEN limit for the Claude **CLI /
  subscription** path (era_ai_accounts), where the transport returns no cost and
  the CLI draws on a flat subscription. A dollar cap is meaningless there, so an
  operational token budget governs consumption instead.

Both are the single source of truth for their path, called from BOTH the inline
guard check (before any spend) and the daily ``cron_check_caps`` safety net.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Cap config parameters (Rule 14 — kept in config, not hardcoded in logic).
_GLOBAL_CAP_PARAM = "era_crm_ai_agents.monthly_cost_cap"
# Per-agent override: _AGENT_CAP_PARAM_PREFIX + agent.tech_name
_AGENT_CAP_PARAM_PREFIX = "era_crm_ai_agents.monthly_cost_cap."

# Token-limit parameters (CLI/subscription path) — same per-agent-overrides-global
# shape as the cost cap. Values are ESTIMATED tokens (see record()).
_GLOBAL_TOKEN_LIMIT_PARAM = "era_crm_ai_agents.monthly_token_limit"
_AGENT_TOKEN_LIMIT_PARAM_PREFIX = "era_crm_ai_agents.monthly_token_limit."


class CrmAiUsage(models.Model):
    _name = "crm.ai.usage"
    _description = "CRM AI Usage"
    _order = "create_date desc"
    _rec_name = "agent_id"

    agent_id = fields.Many2one(
        comodel_name="crm.ai.agent",
        string="Agent",
        required=True,
        ondelete="cascade",
        index=True,
    )
    model_id = fields.Many2one(
        comodel_name="crm.ai.model",
        string="Model",
        ondelete="set null",
        index=True,
    )
    # Captured at call time so the per-user record rule still works even though
    # record() sudo-creates (otherwise create_uid would be the superuser).
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="User",
        ondelete="set null",
        index=True,
        default=lambda self: self.env.uid,
    )
    input_tokens = fields.Integer(string="Input Tokens")
    output_tokens = fields.Integer(string="Output Tokens")
    total_tokens = fields.Integer(
        string="Total Tokens",
        compute="_compute_total_tokens",
        store=True,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id.id,
    )
    cost = fields.Monetary(string="Cost", currency_field="currency_id")
    unpriced = fields.Boolean(
        string="Unpriced",
        help="The model used had no price in the rate card, so this call's cost "
             "could not be computed (recorded as 0). A manager should add a "
             "price for the model so the cap stays accurate (Rule 14).",
    )
    via_cli = fields.Boolean(
        string="CLI / Subscription",
        help="This call ran through the Claude CLI (subscription) path: it has no "
             "per-token dollar cost (cost = 0 by design) and is governed by the "
             "monthly TOKEN limit, not the dollar cap. The token counts are "
             "estimated — do NOT read a 0 cost here as 'free/unlimited'.",
    )
    token_source = fields.Selection(
        selection=[("estimated", "Estimated"), ("measured", "Measured")],
        string="Token Source",
        default="estimated",
        required=True,
        help="How the token counts were obtained. 'Estimated' = length-based "
             "(~1 token / 4 chars): the only option for the CLI path, and also the "
             "case for native API calls because Odoo's public request_llm returns "
             "no provider usage at our seam. 'Measured' = exact provider count "
             "(reserved for if/when a provider usage figure is threaded through).",
    )
    # create_date is provided automatically by Odoo and is the month boundary.

    @api.depends("input_tokens", "output_tokens")
    def _compute_total_tokens(self):
        for usage in self:
            usage.total_tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    @api.model
    def record(self, agent, model, in_tok, out_tok, cost, unpriced=False,
               token_source="estimated", via_cli=False):
        """Create one usage row for an LLM call.

        Approved sudo elevation (create-only): AI users are read-only on this
        model, so record() sudo-creates. user_id is captured from the caller
        before elevating so per-user visibility still works.

        :param unpriced: model had no rate-card price (dollar path; cost = 0).
        :param token_source: 'estimated' (length-based) or 'measured' (provider).
        :param via_cli: True for the Claude CLI/subscription path — cost is 0 by
            design and consumption is governed by the monthly token limit.
        """
        if not agent:
            raise ValueError("crm.ai.usage.record() requires an agent.")
        return self.sudo().create({
            "agent_id": agent.id,
            "model_id": model.id if model else False,
            "user_id": self.env.uid,
            "input_tokens": int(in_tok or 0),
            "output_tokens": int(out_tok or 0),
            "cost": float(cost or 0.0),
            "unpriced": bool(unpriced),
            "token_source": token_source or "estimated",
            "via_cli": bool(via_cli),
        })

    # ------------------------------------------------------------------
    # Cap logic (single source of truth)
    # ------------------------------------------------------------------
    @api.model
    def _get_cap(self, agent):
        """Return the effective monthly cap for an agent, or None if it cannot
        be determined (missing/unparseable) so callers can fail safe.

        Per-agent override wins over the global cap. The config read uses a
        narrow, read-only sudo() — a config lookup, not agent business logic —
        so non-admin callers read the REAL cap instead of hitting AccessError.
        """
        icp = self.env["ir.config_parameter"].sudo()
        raw = None
        if agent.tech_name:
            raw = icp.get_param(_AGENT_CAP_PARAM_PREFIX + agent.tech_name)
        if raw in (None, False, ""):
            raw = icp.get_param(_GLOBAL_CAP_PARAM)
        if raw in (None, False, ""):
            return None  # caller fails safe
        try:
            return float(raw)
        except (TypeError, ValueError):
            _logger.warning("Invalid cost cap value %r; failing safe.", raw)
            return None

    @api.model
    def _current_month_cost(self, agent):
        """Total cost for the agent this calendar month, across ALL users.

        Approved sudo elevation (read/sum only): the per-user usage record rule
        would otherwise make the inline cap undercount (each user sees only their
        own rows). The cap must see the agent's true global total.
        """
        month_start = fields.Date.context_today(self).replace(day=1)
        month_start_dt = fields.Datetime.to_datetime(month_start)
        rows = self.sudo().search([
            ("agent_id", "=", agent.id),
            ("create_date", ">=", month_start_dt),
        ])
        return sum(rows.mapped("cost"))

    @api.model
    def is_over_cap(self, agent):
        """True if the agent's current-month cost is at/over its cap.

        Fails safe: an undeterminable cap blocks (returns True) rather than risk
        silent overspend. A cap of 0 (or negative) is an explicit opt-out.
        """
        cap = self._get_cap(agent)
        if cap is None:
            _logger.warning(
                "Cost cap not readable for agent '%s'; failing safe (blocking).",
                agent.tech_name or agent.id,
            )
            return True
        if cap <= 0:
            return False  # explicit opt-out
        return self._current_month_cost(agent) >= cap

    # ------------------------------------------------------------------
    # Token-limit logic (CLI / subscription path) — mirrors the cost cap
    # ------------------------------------------------------------------
    @api.model
    def _get_token_limit(self, agent):
        """Effective monthly token limit for an agent, or None if undeterminable.

        Per-agent override wins over the global limit. Narrow read-only sudo on the
        config param (a lookup, not agent business logic) so non-admin callers read
        the real limit instead of hitting AccessError.
        """
        icp = self.env["ir.config_parameter"].sudo()
        raw = None
        if agent.tech_name:
            raw = icp.get_param(_AGENT_TOKEN_LIMIT_PARAM_PREFIX + agent.tech_name)
        if raw in (None, False, ""):
            raw = icp.get_param(_GLOBAL_TOKEN_LIMIT_PARAM)
        if raw in (None, False, ""):
            return None  # caller fails safe
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            _logger.warning("Invalid token limit value %r; failing safe.", raw)
            return None

    @api.model
    def _current_month_tokens(self, agent):
        """Total tokens for the agent this calendar month, across ALL users.

        Approved sudo elevation (read/sum only): same rationale as
        ``_current_month_cost`` — the per-user record rule would undercount.
        """
        month_start = fields.Date.context_today(self).replace(day=1)
        month_start_dt = fields.Datetime.to_datetime(month_start)
        rows = self.sudo().search([
            ("agent_id", "=", agent.id),
            ("create_date", ">=", month_start_dt),
        ])
        return sum(rows.mapped("total_tokens"))

    @api.model
    def is_over_token_limit(self, agent):
        """True if the agent's current-month (estimated) tokens are at/over limit.

        Fails safe: an undeterminable limit blocks (returns True) rather than let
        the subscription path run unbounded. A limit of 0 (or negative) is an
        explicit opt-out (unbounded), mirroring the cost cap.
        """
        limit = self._get_token_limit(agent)
        if limit is None:
            _logger.warning(
                "Token limit not readable for agent '%s'; failing safe (blocking).",
                agent.tech_name or agent.id,
            )
            return True
        if limit <= 0:
            return False  # explicit opt-out
        return self._current_month_tokens(agent) >= limit

    # ------------------------------------------------------------------
    # Daily safety net
    # ------------------------------------------------------------------
    @api.model
    def _is_over_limit(self, agent):
        """Per-agent consumption check across BOTH paths: dollar cost cap for
        API agents, estimated-token limit for CLI/subscription agents."""
        if agent.transport == "cli":
            return self.is_over_token_limit(agent)
        return self.is_over_cap(agent)

    @api.model
    def cron_check_caps(self):
        """Daily reconciliation: pause agents over their limit, and re-enable
        previously capped agents that have dropped back under (e.g. month reset).

        Uses the per-agent path (cost cap for API agents, token limit for CLI
        agents). Only touches the system-managed 'capped'/'enabled' transition —
        agents an admin manually 'paused' are left alone.
        """
        Agent = self.env["crm.ai.agent"]
        for agent in Agent.search([("state", "=", "enabled")]):
            if self._is_over_limit(agent):
                agent.state = "capped"
                _logger.info("Agent '%s' paused: over monthly limit.", agent.tech_name)
        for agent in Agent.search([("state", "=", "capped")]):
            if not self._is_over_limit(agent):
                agent.state = "enabled"
                _logger.info("Agent '%s' re-enabled: back under limit.", agent.tech_name)
        return True
