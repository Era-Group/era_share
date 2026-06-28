# -*- coding: utf-8 -*-
"""Compliance + human-approval wiring (task 3.5) — the pre-send pipeline.

    review_and_route(lead, trigger) -> crm.ai.approval | False

Ties together the pieces built earlier, in the only order that is safe:

    detect trigger (3.3) -> draft (3.4) -> compliance guard() -> human approval

Hard guarantees (this task's acceptance):

* **No send happens here.** This pipeline only ever creates a *pending*
  crm.ai.approval. The actual WhatsApp send runs solely in
  ``crm.ai.dead.lead.agent._ai_on_approved`` (task 3.6), which the approval
  layer fires ONLY after a human approves. So nothing leaves without approval.
* **Every decision is logged (Rule 20).** Compliance ``guard()`` writes exactly
  one audit row per call (allowed or blocked); ``_request_human_approval`` logs
  ``approval_requested``; and the edge cases below add their own audit row.
* A lead with **no linked partner** is blocked: PDPL consent cannot be verified
  without a res.partner, so we never route it.

Runs under the calling salesperson (NO sudo) — Rule 09. ``unattended`` is
threaded to drafting so a guard block in the cron run (3.7) is dropped-with-audit
instead of raising.
"""
import logging

_logger = logging.getLogger(__name__)


class ApprovalGate:
    CHANNEL = "whatsapp"

    def __init__(self, agent, unattended=True):
        agent.ensure_one()
        self.agent = agent
        self.env = agent.env
        self.unattended = unattended

    # ------------------------------------------------------------------
    def review_and_route(self, lead, trigger=None):
        """Draft → compliance guard → human approval. Returns the pending
        crm.ai.approval, or False when the lead is not routed (not qualifying /
        no partner / no draft / compliance block). Never sends."""
        trigger = trigger or self.agent.detect_trigger(lead)
        if not trigger:
            return False

        partner = lead.partner_id
        if not partner:
            # Without a partner there is no consent record to check -> never send.
            self.agent._log_critical(
                "blocked", record=lead,
                after={"event": "dead_lead_route", "channel": self.CHANNEL,
                       "reason": "no linked partner (PDPL consent unverifiable)"})
            return False

        text = self.agent.draft_message(lead, trigger, unattended=self.unattended)
        if not text:
            self.agent._log_critical(
                "other", record=lead,
                after={"event": "dead_lead_route", "channel": self.CHANNEL,
                       "reason": "no draft produced (no template / empty)"})
            return False

        # Compliance gate — consent + send-window + cultural norms. It writes its
        # own audit row for the decision (Rule 20). A block (incl. a timing
        # defer) means: do NOT route, do NOT send; the scan (3.7) re-evaluates
        # on its next run.
        decision = self._guard(partner, text)
        if not decision.get("allowed"):
            return False

        # Allowed -> open the human gate. Creates a PENDING approval and logs
        # 'approval_requested'. The send fires only from _ai_on_approved (3.6)
        # after a human approves.
        return self.agent._request_human_approval(text, record=lead)

    # ------------------------------------------------------------------
    def _guard(self, partner, text):
        """Call the cross-cutting compliance guard (declared dependency).

        Lazy import keeps compliance out of the load path until a send is
        actually evaluated. consent_type defaults to the compliance module's
        configured marketing consent.
        """
        from odoo.addons.era_crm_ai_agents_compliance.services.guard import (
            guard as compliance_guard,
        )
        return compliance_guard(self.env, partner, text, channel=self.CHANNEL)
