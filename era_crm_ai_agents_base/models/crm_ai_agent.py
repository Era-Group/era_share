# -*- coding: utf-8 -*-
from odoo import api, fields, models


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

    # Forward reference: crm.ai.model is introduced in task 0.2. The module
    # becomes installable once that model exists (install gate is task 0.10).
    default_model_id = fields.Many2one(
        comodel_name="crm.ai.model",
        string="Default Model",
        ondelete="set null",
        help="Default LLM the agent uses unless the router overrides it by "
             "task sensitivity.",
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
             "month, summed from crm.ai.usage.",
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
