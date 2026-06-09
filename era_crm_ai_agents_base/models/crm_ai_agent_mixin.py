# -*- coding: utf-8 -*-
"""The heart of the architecture.

``crm.ai.agent.mixin`` is an AbstractModel every concrete agent inherits to get,
for free: a convenient LLM call that runs through Odoo's NATIVE AI (so the AI
Compliance Guard governs it), critical audit logging (Rule 20), and the human
approval gate.

Architecture (final): we do NOT call providers ourselves and we do NOT route by
provider. Every LLM call goes through Odoo 19 native ``ai.utils.LLMApiService``;
the AI Compliance Guard (services/ai_compliance_guard.py) is monkeypatched onto
that service and enforces — pre-flight, before any outbound call — PDPL consent +
record-driven PII redaction, the hard cost cap (Rule 14), env-only keys
(Rule 03), and the persistent audit (Rule 20). This mixin's only job for an LLM
call is to (a) pick a catalog model for the requested sensitivity and (b) stamp
the context the guard needs (which agent, which record, attended vs unattended).

Design rule honored here:
- NO sudo / superuser anywhere in this mixin. Records are read/written under the
  calling user (Rule 09 / 19). Elevation needed for audit/usage/cap/state is
  delegated to narrow helpers on the target models (the guard records usage; the
  audit model sudo-creates its immutable row).
"""
from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.ai.utils.llm_api_service import LLMApiService

from odoo.addons.era_crm_ai_agents_base.services.ai_compliance_guard import (
    CTX_AGENT,
    CTX_RECORD,
    CTX_UNATTENDED,
)


class CrmAiAgentMixin(models.AbstractModel):
    _name = "crm.ai.agent.mixin"
    _description = "CRM AI Agent Mixin"

    # Each concrete agent sets this to its stable registry key (crm.ai.agent.tech_name).
    _agent_tech_name = None

    # ------------------------------------------------------------------
    # Public API (called by concrete agents)
    # ------------------------------------------------------------------
    def _call_llm(self, prompt, sensitivity="low", system=None, record=None,
                  unattended=False, max_output_tokens=1024):
        """Run one LLM call through native Odoo AI under the Compliance Guard.

        The guard enforces consent, PII redaction, the cost cap, env-only keys,
        and audit/usage — this method just selects the model and hands the guard
        the context it needs. Real PII is masked before egress and restored in the
        returned text by the guard.

        :param prompt: the user prompt (str).
        :param sensitivity: 'high' -> advanced model, else cheap (Rule 14 default).
        :param system: optional system prompt (str).
        :param record: the record this call is about (res.partner / crm.lead / …).
            Drives record-driven redaction and consent. Strongly recommended
            whenever personal data is involved.
        :param unattended: True for cron/batch flows — on a guard block the call
            is dropped-with-audit and an empty result returns (the batch
            continues; PII is never sent). False (default) raises on a block.
        :param max_output_tokens: advisory cap on output length.
        :returns: list[str] — the native response message(s), PII restored.
        :raises UserError: when the guard blocks an attended call (no consent,
            unmappable PII, cost cap) — see services/ai_compliance_guard.py.
        """
        agent = self._get_agent_record()
        # Model selection lives on the agent (explicit code per sensitivity);
        # provider is derived from the code. Pricing for Rule 14 comes from the
        # rate card (crm.ai.model) by that code.
        provider, code = agent._model_for_sensitivity(sensitivity)

        ctx = {CTX_AGENT: agent.tech_name}
        if record is not None:
            ctx[CTX_RECORD] = record
        if unattended:
            ctx[CTX_UNATTENDED] = True

        service = LLMApiService(env=self.with_context(**ctx).env, provider=provider)
        return service.request_llm(
            code,
            [system] if system else [],
            [prompt],
            temperature=0.2,
        )

    def _check_cost_cap(self, agent=None):
        """Advisory pre-check: raise if this agent is paused/capped or over cap.

        Authoritative enforcement happens in the guard at call time (it also
        auto-pauses and audits). Agents may call this to fail fast before doing
        expensive prep work.
        """
        agent = agent or self._get_agent_record()
        if agent.state in ("paused", "capped"):
            raise UserError(
                _("AI agent '%(name)s' is %(state)s and cannot run.",
                  name=agent.name, state=agent.state))
        if self.env["crm.ai.usage"].is_over_cap(agent):
            raise UserError(
                _("AI agent '%(name)s' is over its monthly cost cap (Rule 14).",
                  name=agent.name))
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
