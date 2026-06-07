# -*- coding: utf-8 -*-
"""Per-call cost tracking, monthly aggregation, and the hard cost cap (Rule 14).

``is_over_cap`` is the single source of truth for the cap, called from BOTH the
inline check in crm.ai.agent.mixin._call_llm (before any provider spend) and the
daily ``cron_check_caps`` safety net.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Cap config parameters (Rule 14 — kept in config, not hardcoded in logic).
_GLOBAL_CAP_PARAM = "era_crm_ai_agents.monthly_cost_cap"
# Per-agent override: _AGENT_CAP_PARAM_PREFIX + agent.tech_name
_AGENT_CAP_PARAM_PREFIX = "era_crm_ai_agents.monthly_cost_cap."


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
    # create_date is provided automatically by Odoo and is the month boundary.

    @api.depends("input_tokens", "output_tokens")
    def _compute_total_tokens(self):
        for usage in self:
            usage.total_tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    @api.model
    def record(self, agent, model, in_tok, out_tok, cost):
        """Create one usage row for an LLM call.

        Approved sudo elevation (create-only): AI users are read-only on this
        model, so record() sudo-creates. user_id is captured from the caller
        before elevating so per-user visibility still works.
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
    # Daily safety net
    # ------------------------------------------------------------------
    @api.model
    def cron_check_caps(self):
        """Daily reconciliation: pause agents over cap, and re-enable previously
        capped agents that have dropped back under cap (e.g. a new month reset).

        Only touches the system-managed 'capped'/'enabled' transition — agents an
        admin manually 'paused' are left alone.
        """
        Agent = self.env["crm.ai.agent"]
        for agent in Agent.search([("state", "=", "enabled")]):
            if self.is_over_cap(agent):
                agent.state = "capped"
                _logger.info("Agent '%s' paused: over monthly cost cap.", agent.tech_name)
        for agent in Agent.search([("state", "=", "capped")]):
            if not self.is_over_cap(agent):
                agent.state = "enabled"
                _logger.info("Agent '%s' re-enabled: back under cap.", agent.tech_name)
        return True
