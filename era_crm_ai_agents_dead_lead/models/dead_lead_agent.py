# -*- coding: utf-8 -*-
"""The Dead-Lead Resurrection agent — registration + configuration.

``crm.ai.dead.lead.agent`` is a concrete, manager-editable configuration model
that inherits the shared ``crm.ai.agent.mixin``. Inheriting the mixin gives it
the whole agent toolbox without any sudo or provider code of its own:

* ``_call_llm(...)`` — the ONLY way the agent reaches an LLM (stamps CTX_AGENT so
  the Base AI Compliance Guard runs consent/redaction/limit/audit), used by the
  drafting engine in task 3.4 at ``sensitivity='high'``;
* ``_check_cost_cap()`` / ``_log_critical(...)`` / ``_request_human_approval(...)``
  / ``_ai_on_approved(...)`` — cap guard, audit log, and the human gate used by
  tasks 3.4–3.6.

``_agent_tech_name`` ties this model to the pre-seeded ``crm.ai.agent`` registry
row (``tech_name='era_dead_lead'``), which the mixin resolves via
``_get_agent_record()``. The behavioural logic (classification, trigger
detection, drafting, compliance/approval wiring, sending, scheduling) arrives in
tasks 3.3–3.7; this task only registers the agent and its configuration.
"""
import logging
from datetime import timedelta

from odoo import fields, models

_logger = logging.getLogger(__name__)


class CrmAiDeadLeadAgent(models.Model):
    _name = "crm.ai.dead.lead.agent"
    _inherit = ["crm.ai.agent.mixin"]
    _description = "Dead-Lead Resurrection Agent"

    # Ties this config model to its crm.ai.agent registry row (seeded on
    # install). The mixin uses it for _call_llm / cap / audit / approval.
    _agent_tech_name = "era_dead_lead"

    name = fields.Char(
        default="Dead-Lead Resurrection", required=True, translate=True,
        help="Display label for this agent configuration.")

    # --- Configuration (policy values — kept out of code per No-Hardcoded-Policy)
    elapsed_days_threshold = fields.Integer(
        default=180,
        help="Minimum number of days a lead must have been closed-lost before "
             "the 'enough time elapsed' trigger may fire (task 3.3). A manager "
             "may change this; the default is a safe, conservative window.")
    template_id = fields.Many2one(
        comodel_name="crm.ai.dead.lead.template",
        ondelete="restrict",
        help="The approved comeback template the agent fills the personal parts "
             "of when drafting (task 3.4).")

    # --- Scheduling (task 3.7) — manager-editable, kept off the code (No-
    #     Hardcoded-Policy). Read by the cron from this config record (no sudo;
    #     the run-user has read ACL on this model from task 3.2).
    scan_enabled = fields.Boolean(
        string="Enable Scheduled Scan", default=False,
        help="Master switch for the daily scan. OFF by default — a sending agent "
             "stays fully inert until a manager opts in. When off, the cron runs "
             "but does nothing.")
    scan_batch_size = fields.Integer(
        string="Scan Batch Size", default=50,
        help="Maximum number of lost leads to process per scheduled run "
             "(bounds work and cost per run).")
    cooldown_days = fields.Integer(
        string="Resend Cooldown (days)", default=30,
        help="Minimum days after a comeback was sent (or proposed) before the "
             "same lead may be scanned again. Prevents re-contacting the same "
             "lead on every run.")

    # ------------------------------------------------------------------
    # Classification + trigger detection (task 3.3) — thin delegators onto the
    # read-only, no-sudo TriggerDetector service. Lazy import so the services
    # package loads only when detection is actually run.
    # ------------------------------------------------------------------
    def _trigger_detector(self):
        self.ensure_one()
        from odoo.addons.era_crm_ai_agents_dead_lead.services.trigger_detect import (
            TriggerDetector,
        )
        return TriggerDetector(self)

    def classify_lost(self, lead):
        """Lost reason -> manager-configured bucket (str)."""
        return self._trigger_detector().classify_lost(lead)

    def detect_trigger(self, lead):
        """Trigger dict for a qualifying lost lead, else None."""
        return self._trigger_detector().detect_trigger(lead)

    # ------------------------------------------------------------------
    # Message drafting (task 3.4) — template-bound; AI fills only slots.
    # ------------------------------------------------------------------
    def draft_message(self, lead, trigger, unattended=False):
        """Assemble the template-bound comeback text for (lead, trigger).

        ``unattended=True`` (cron) makes a guard block drop-with-audit (empty
        personal note) instead of raising. Lazy import keeps the services
        package out of the load path until drafting actually runs.
        """
        self.ensure_one()
        from odoo.addons.era_crm_ai_agents_dead_lead.services.draft import DraftEngine
        return DraftEngine(self, unattended=unattended).draft_message(lead, trigger)

    # ------------------------------------------------------------------
    # Compliance + approval wiring (task 3.5) — draft -> guard() -> human gate.
    # ------------------------------------------------------------------
    def review_and_route(self, lead, trigger=None, unattended=True):
        """Run a lead through draft -> compliance guard -> human approval.

        Returns the pending crm.ai.approval, or False if not routed. NEVER
        sends — the send happens only in _ai_on_approved (3.6) after approval.
        """
        self.ensure_one()
        from odoo.addons.era_crm_ai_agents_dead_lead.services.approval_gate import (
            ApprovalGate,
        )
        return ApprovalGate(self, unattended=unattended).review_and_route(lead, trigger)

    # ------------------------------------------------------------------
    # Send (task 3.6) — fired by the approval layer AFTER a human approves.
    # ------------------------------------------------------------------
    def _ai_on_approved(self, approval):
        """Send the approved comeback once a human approves (overrides the mixin
        no-op). The approval carries the reviewed text and the lead; anything
        else is left to the base hook."""
        lead = approval.record_ref
        if lead and lead._name == "crm.lead":
            return self.send_via_waha(lead, approval.effective_content)
        return super()._ai_on_approved(approval)

    def send_via_waha(self, lead, text):
        """Send ``text`` to the lead's WhatsApp via the WAHA connector, then
        update the lead and write a masked audit entry. Returns True on success.

        Tolerates being called on the empty model recordset (the approval
        callback's caller) — the sender uses only env + the mixin audit helper.
        """
        from odoo.addons.era_crm_ai_agents_dead_lead.services.send import WahaSender
        return WahaSender(self).send_via_waha(lead, text)

    # ------------------------------------------------------------------
    # Scheduling (task 3.7) — the daily scan. Invoked by ir.cron as
    #   model.with_user(crm.ai.agent._get_cron_run_user()).cron_scan_dead_leads()
    # so it runs under the configured least-privilege identity, NEVER root
    # (Rule 09). The scan therefore only sees leads that run-user may see; a safe
    # default install (least-privilege user + scan_enabled OFF) is fully inert.
    # ------------------------------------------------------------------
    def cron_scan_dead_leads(self):
        """Route a cost-cap-bounded batch of qualifying lost leads to approval.

        For each candidate: detect a trigger (3.3) -> draft (3.4) -> compliance
        guard -> human approval (3.5), all unattended. NOTHING is sent here (send
        happens only after a human approves, 3.6). The loop STOPS the instant the
        Rule-14 cost cap trips. Returns the number of leads routed.
        """
        config = self.search([], limit=1)
        if not config or not config.scan_enabled:
            return 0  # master switch OFF (or unconfigured) -> fully inert

        agent = self._get_agent_record()
        Usage = self.env["crm.ai.usage"]
        # Cap pre-check: if already over, don't even open the batch.
        if Usage._is_over_limit(agent):
            config._log_critical(
                "cost_cap_exceeded",
                after={"event": "dead_lead_cron_capped", "stage": "pre-check"})
            return 0

        leads = self._scan_candidates(config)
        routed = 0
        for lead in leads:
            if Usage._is_over_limit(agent):
                config._log_critical(
                    "cost_cap_exceeded", record=lead,
                    after={"event": "dead_lead_cron_capped", "routed": routed})
                break
            if config._has_open_proposal(agent, lead):
                continue  # an approval is already pending for this lead
            if config.review_and_route(lead, unattended=True):
                routed += 1
        _logger.info(
            "Dead-Lead cron: routed %s of %s scanned lead(s).", routed, len(leads))
        return routed

    def _scan_candidates(self, config):
        """Closed-lost, contactable leads not in cooldown — bounded by batch size
        and (crucially) by the run-user's own record-rule visibility (Rule 09).
        Reason-eligibility and the elapsed-time threshold are re-checked per lead
        in detect_trigger, so they are not duplicated in this domain."""
        cooldown_cutoff = fields.Datetime.now() - timedelta(
            days=max(config.cooldown_days or 0, 0))
        domain = [
            ("active", "=", False),          # lost == archived ...
            ("probability", "<=", 0),        # ... AND probability 0 (crm core)
            ("partner_id", "!=", False),     # need a partner for consent + phone
            "|",
            ("crm_ai_dead_lead_last_sent", "=", False),
            ("crm_ai_dead_lead_last_sent", "<", cooldown_cutoff),
        ]
        return (
            self.env["crm.lead"]
            .with_context(active_test=False)  # lost leads are archived
            .search(domain, order="create_date asc",
                    limit=max(config.scan_batch_size or 0, 0))
        )

    def _has_open_proposal(self, agent, lead):
        """True if a pending approval from this agent already references the lead.

        NARROW READ-ONLY SUDO: the least-privilege cron user only sees approvals
        where it is the reviewer (record rule), so it cannot otherwise detect a
        pending approval owned by the lead's salesperson. This sudo does exactly
        one thing — an existence count — and writes nothing.
        """
        return bool(self.env["crm.ai.approval"].sudo().search_count([
            ("agent_id", "=", agent.id),
            ("state", "=", "pending"),
            ("record_ref", "=", "crm.lead,%d" % lead.id),
        ]))
