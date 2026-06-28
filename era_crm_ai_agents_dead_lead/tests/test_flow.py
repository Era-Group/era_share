# -*- coding: utf-8 -*-
"""Task 3.9 — end-to-end flow: detect -> draft -> approve -> send.

The acceptance is "End-to-end passes with the approval gate enforced", so the
load-bearing assertions are:

* a qualifying lost lead is DETECTED and routed to a PENDING approval;
* NO send happens until a human approves (the lead is never stamped, the
  connector is never called, before approval);
* approving fires the send exactly once and updates the lead + writes an audit
  row;
* when compliance blocks, NO approval is created and NO send happens.

Tests are fully OFFLINE and deterministic:
* the LLM router (``_call_llm``) is patched to a canned note — no provider, no
  cost, no egress;
* the cross-cutting compliance ``guard()`` is patched per-test to allow/block
  (it has its own suite in Module 1) so we isolate OUR flow + the approval gate;
* the WAHA connector is replaced by a fake session that records ``send_message``
  — no WhatsApp/Meta egress.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.era_crm_ai_agents_dead_lead.services.send import WahaSender

_CANNED_NOTE = "يسعدنا التواصل معكم مجدداً بخصوص اهتمامكم السابق."
_GUARD = "odoo.addons.era_crm_ai_agents_compliance.services.guard.guard"


def _allow(*args, **kwargs):
    return {"allowed": True, "reason": "allowed", "deferred_until": None}


def _block(*args, **kwargs):
    return {"allowed": False, "reason": "no marketing consent (PDPL)",
            "deferred_until": None}


class _FakeSession:
    """Stand-in for sadeem.waha.session — records sends instead of egressing."""

    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"id": "fake"}


@tagged("post_install", "-at_install")
class TestDeadLeadFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env["crm.ai.dead.lead.agent"].search([], limit=1)
        assert cls.agent, "agent config must be seeded on install"
        # Use a slotted template so {{personal_note}} is the AI-filled slot.
        cls.template = cls.env.ref(
            "era_crm_ai_agents_dead_lead.dead_lead_tmpl_general")
        cls.agent.write({
            "template_id": cls.template.id,
            "elapsed_days_threshold": 180,
            "scan_enabled": True,
            "cooldown_days": 30,
        })
        cls.reason = cls.env["crm.lost.reason"].create({
            "name": "Test: Too expensive",
            "crm_ai_reason_bucket": "price",
            "crm_ai_resurrectable": True,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "أحمد التجريبي",
            "phone": "+966500000000",
        })

    def _make_lost_lead(self, days_ago=400, reason=None, partner=None):
        lead = self.env["crm.lead"].create({
            "name": "Test Opportunity",
            "partner_id": (partner or self.partner).id,
            "user_id": self.env.user.id,
            "lost_reason_id": (reason or self.reason).id,
        })
        # Lost semantics (crm core): archived + probability 0. Core's write()
        # forces date_closed=now when active flips False, so back-date it in a
        # SEPARATE write (where that clobber branch does not fire).
        lead.write({"active": False, "probability": 0})
        lead.write({
            "date_closed": fields.Datetime.now() - timedelta(days=days_ago)})
        return lead

    # ------------------------------------------------------------------
    def test_01_detect_trigger_only_for_qualifying(self):
        """detect_trigger fires for a lost+eligible+elapsed lead, not otherwise."""
        old = self._make_lost_lead(days_ago=400)
        trig = self.agent.detect_trigger(old)
        self.assertTrue(trig, "an old, eligible lost lead must trigger")
        self.assertEqual(trig["type"], "elapsed_time")
        self.assertEqual(trig["reason_bucket"], "price")

        recent = self._make_lost_lead(days_ago=10)
        self.assertIsNone(self.agent.detect_trigger(recent),
                          "a recently-lost lead must NOT trigger")

        opted_out = self.env["crm.lost.reason"].create({
            "name": "Do not contact", "crm_ai_resurrectable": False})
        blocked = self._make_lost_lead(days_ago=400, reason=opted_out)
        self.assertIsNone(self.agent.detect_trigger(blocked),
                          "an opted-out reason must NOT trigger")

    def test_02_draft_is_template_bound(self):
        """Only {{personal_note}} is AI-filled; fixed lines stay verbatim."""
        lead = self._make_lost_lead()
        trig = self.agent.detect_trigger(lead)
        with patch.object(type(self.agent), "_call_llm",
                          lambda self, *a, **k: [_CANNED_NOTE]):
            text = self.agent.draft_message(lead, trig, unattended=True)
        self.assertIn(_CANNED_NOTE, text)            # AI slot filled
        self.assertIn("مع خالص التقدير", text)        # fixed template line intact
        self.assertNotIn("{{", text)                  # no unfilled/injected slots

    def test_03_full_flow_gate_enforced(self):
        """detect -> draft -> approve -> send, with NO send before approval."""
        lead = self._make_lost_lead()
        fake = _FakeSession()

        # Route to approval (draft + guard allowed). NO send must happen here.
        with patch.object(type(self.agent), "_call_llm",
                          lambda self, *a, **k: [_CANNED_NOTE]), \
                patch(_GUARD, side_effect=_allow):
            approval = self.agent.review_and_route(lead, unattended=True)

        self.assertTrue(approval, "an allowed lead must produce an approval")
        self.assertEqual(approval.state, "pending")
        self.assertEqual(approval.agent_id.tech_name, "era_dead_lead")
        self.assertEqual(approval.record_ref, lead)
        # GATE: nothing sent yet.
        self.assertFalse(lead.crm_ai_dead_lead_last_sent,
                         "lead must NOT be sent before approval")
        self.assertEqual(fake.sent, [], "connector must NOT be called pre-approval")

        audits_before = self.env["crm.ai.audit.log"].search_count(
            [("event_type", "=", "send")])

        # Human approves -> send fires exactly once.
        with patch.object(WahaSender, "_ready_session", lambda self: fake):
            approval.action_approve()

        self.assertEqual(approval.state, "approved")
        self.assertEqual(len(fake.sent), 1, "exactly one send on approval")
        chat_id, sent_text = fake.sent[0]
        self.assertEqual(chat_id, "966500000000@c.us")
        self.assertEqual(sent_text, approval.effective_content)
        self.assertTrue(lead.crm_ai_dead_lead_last_sent,
                        "lead must be stamped after send")
        self.assertGreater(
            self.env["crm.ai.audit.log"].search_count([("event_type", "=", "send")]),
            audits_before, "a 'send' audit row must be written")

    def test_04_compliance_block_no_approval_no_send(self):
        """A compliance block routes nothing and sends nothing (gate enforced)."""
        lead = self._make_lost_lead()
        with patch.object(type(self.agent), "_call_llm",
                          lambda self, *a, **k: [_CANNED_NOTE]), \
                patch(_GUARD, side_effect=_block):
            result = self.agent.review_and_route(lead, unattended=True)

        self.assertFalse(result, "a blocked lead must not be routed")
        self.assertEqual(
            self.env["crm.ai.approval"].search_count([
                ("agent_id.tech_name", "=", "era_dead_lead"),
                ("record_ref", "=", "crm.lead,%d" % lead.id)]),
            0, "no approval may be created when compliance blocks")
        self.assertFalse(lead.crm_ai_dead_lead_last_sent)

    def test_05_cron_routes_but_never_sends(self):
        """The scheduled scan creates approvals but never sends (human-gated)."""
        lead = self._make_lost_lead()
        fake = _FakeSession()
        with patch.object(type(self.agent), "_call_llm",
                          lambda self, *a, **k: [_CANNED_NOTE]), \
                patch(_GUARD, side_effect=_allow), \
                patch.object(WahaSender, "_ready_session", lambda self: fake):
            routed = self.env["crm.ai.dead.lead.agent"].cron_scan_dead_leads()

        self.assertGreaterEqual(routed, 1, "cron must route the qualifying lead")
        self.assertEqual(fake.sent, [], "cron must NEVER send (approval-gated)")
        self.assertEqual(
            self.env["crm.ai.approval"].search_count([
                ("agent_id.tech_name", "=", "era_dead_lead"),
                ("record_ref", "=", "crm.lead,%d" % lead.id),
                ("state", "=", "pending")]),
            1, "cron must create exactly one pending approval for the lead")
