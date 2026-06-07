# -*- coding: utf-8 -*-
"""The heart of the architecture.

``crm.ai.agent.mixin`` is an AbstractModel every concrete agent inherits to get,
for free: a unified LLM call, a pre-call hard cost-cap check (Rule 14), critical
audit logging (Rule 20), and the human approval gate.

Design rule honored here:
- NO sudo / superuser anywhere in this mixin. Every record is read and written
  under the calling user's permissions (Rule 09 / 19). Where an operation needs
  elevation (audit create, usage record, agent state/last_run writes, cap reads)
  it is delegated to a narrow, single-purpose helper on the target model — never
  done here. Security task 0.8 grants the AI groups the access they need;
  satellite modules should pre-seed their agent record so runtime stays read-only.
"""
from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.era_crm_ai_agents_base.services.llm_router import (
    LLMRouter,
    LLMRouterError,
)


class CrmAiAgentMixin(models.AbstractModel):
    _name = "crm.ai.agent.mixin"
    _description = "CRM AI Agent Mixin"

    # Each concrete agent sets this to its stable registry key (crm.ai.agent.tech_name).
    _agent_tech_name = None

    # ------------------------------------------------------------------
    # Public API (called by concrete agents)
    # ------------------------------------------------------------------
    def _call_llm(self, prompt, sensitivity="low", **kw):
        """Run one LLM call for this agent, enforcing the cap and recording usage.

        Order is strict: check the cap FIRST (never spend then check), then call
        the router, then record usage. Returns the router result dict
        ``{text, input_tokens, output_tokens, cost, model}``.

        :raises UserError: if the agent is paused/capped or over its cost cap.
        :raises LLMRouterError: if the provider call fails (already clean — the
            router converts raw network/provider errors; we log then re-raise it
            so callers can catch a single, typed, secret-free exception).
        """
        agent = self._get_agent_record()
        # 1. Hard cost-cap gate — blocks BEFORE any provider spend (Rule 14).
        self._check_cost_cap(agent=agent)
        # 2. Call the provider.
        try:
            result = LLMRouter(self.env).call(prompt, sensitivity=sensitivity, **kw)
        except LLMRouterError as exc:
            self._log_critical("llm_call_failed", agent, {"sensitivity": sensitivity}, {"error": str(exc)})
            raise
        # 3. Record usage (feeds the cap and the cost dashboards).
        self._record_usage(agent, result)
        agent._record_run()
        return result

    def _check_cost_cap(self, agent=None):
        """Raise (and auto-pause) if the agent is over its monthly cost cap.

        Enforced inline here on every call — not only by a cron (Rule 14).
        """
        agent = agent or self._get_agent_record()
        if agent.state in ("paused", "capped"):
            raise UserError(
                _("AI agent '%(name)s' is %(state)s and cannot run.",
                  name=agent.name, state=agent.state)
            )
        # Single source of truth: crm.ai.usage.is_over_cap (same check the
        # daily cron uses). Fails safe if the cap is unreadable.
        if self.env["crm.ai.usage"].is_over_cap(agent):
            # Auto-pause and record the breach before blocking.
            agent._mark_state("capped")
            self._log_critical(
                "cost_cap_exceeded", agent,
                {"monthly_cost": agent.monthly_cost}, {"capped": True},
            )
            raise UserError(
                _("AI agent '%(name)s' has hit its monthly cost cap and has "
                  "been paused.", name=agent.name)
            )
        return True

    def _log_critical(self, event_type, record=None, before=None, after=None):
        """Append a critical-decision entry to the audit log (Rule 20).

        Delegates to crm.ai.audit.log.log(), which sudo-creates the immutable
        row (approved elevation) so logging always succeeds.
        """
        agent = self._get_agent_record()
        return self.env["crm.ai.audit.log"].log(event_type, agent, record, before, after)

    def _request_human_approval(self, content, record=None):
        """Open the human approval gate for a proposed action (e.g. a message).

        Routes to the record's salesperson when available, else the current user.
        Returns the crm.ai.approval record.
        """
        agent = self._get_agent_record()
        reviewer = self._approval_reviewer(record)
        approval = self.env["crm.ai.approval"].create_request(
            agent, content, record=record, reviewer=reviewer, source_model=self._name,
        )
        self._log_critical("approval_requested", record, None, {"approval_id": approval.id})
        return approval

    def _approval_reviewer(self, record):
        """The human who should sign off: the record's salesperson if it has one,
        otherwise the current user."""
        if record is not None and record.id and "user_id" in record._fields:
            if record.user_id:
                return record.user_id
        return self.env.user

    def _ai_on_approved(self, approval):
        """Hook fired by crm.ai.approval.action_approve when a human approves a
        request from this agent.

        No-op in the base. Concrete agents override it to perform the actual
        send/action using ``approval.effective_content`` and ``approval.record_ref``.
        """
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_agent_record(self):
        """Resolve this agent's crm.ai.agent registry record by _agent_tech_name.

        Find-or-create (no sudo): at runtime the record is expected to already
        exist (pre-seeded on install), so this is a read; creation only happens
        once, under whoever first triggers it.
        """
        tech_name = getattr(self, "_agent_tech_name", None)
        if not tech_name:
            raise UserError(
                _("AI agent %s has no _agent_tech_name set.", self._name)
            )
        return self.env["crm.ai.agent"]._get_or_create_agent(
            tech_name, {"name": tech_name}
        )

    def _record_usage(self, agent, result):
        """Record a crm.ai.usage row for this call via the usage model (no sudo)."""
        # The router returns the model *code*; map it to the catalog record.
        model = False
        model_code = result.get("model")
        if model_code:
            model = self.env["crm.ai.model"].search(
                [("code", "=", model_code)], limit=1
            )
        return self.env["crm.ai.usage"].record(
            agent, model or False,
            result.get("input_tokens", 0),
            result.get("output_tokens", 0),
            result.get("cost", 0.0),
        )
