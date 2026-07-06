# -*- coding: utf-8 -*-
"""The 17 acceptance tests from the module spec, in spec order."""
import json
from datetime import timedelta
from unittest import mock

from odoo import fields
from odoo.tests import tagged

from .common import CampaignCase


@tagged("post_install", "-at_install")
class TestCampaignAgent(CampaignCase):

    # 1 ------------------------------------------------------------------
    def test_01_agent_disabled_cron_noop(self):
        self._set("enabled", "False")
        self._partner("Eligible One", "one@customer.example")
        result, calls = self._run(unattended=True)
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(self.Campaign.search([]))
        self.assertFalse(calls, "disabled agent must make no LLM call")

    # 2 ------------------------------------------------------------------
    def test_02_pdpl_on_compliance_absent_skips_partner(self):
        if "crm.ai.consent" in self.env:
            self.skipTest("Compliance module installed — this test asserts "
                          "the absent-guard behavior on a clean DB.")
        self._set("pdpl_guard_enabled", "True")
        partner = self._partner("No Guard", "noguard@customer.example")
        result, calls = self._run()
        self.assertEqual(result["status"], "no_eligible_partners")
        line = self.Line.search([("partner_id", "=", partner.id)])
        self.assertEqual(line.state, "skipped")
        self.assertEqual(line.skip_reason, "no_consent_guard")
        self.assertFalse(calls, "no LLM call for a consent-skipped partner")
        self.assertFalse(self.Mailing.search([]))

    # 3 ------------------------------------------------------------------
    def test_03_pdpl_off_consent_check_skipped(self):
        self._set("pdpl_guard_enabled", "False")
        partner = self._partner("No Consent Needed", "off@customer.example")
        result, _calls = self._run()
        self.assertEqual(result["status"], "ok")
        line = self.Line.search([("partner_id", "=", partner.id)])
        self.assertEqual(line.state, "sent",
                         "with the PDPL guard OFF the partner is eligible "
                         "without any consent verification")

    # 4 ------------------------------------------------------------------
    def test_04_pdpl_on_egress_payload_has_no_pii(self):
        self._set("pdpl_guard_enabled", "True")
        partner = self._partner(
            "Mohammed Alqahtani", "mohammed.q@customer.example",
            phone="+966501234567")
        # Simulate the Compliance layer approving consent so the partner
        # reaches the LLM stage (the minimization under test).
        from odoo.addons.era_crm_ai_agents_campaign.services.campaign_engine import (
            CampaignEngine,
        )
        with mock.patch.object(CampaignEngine, "_consent_check",
                               return_value=(True, "")):
            result, calls = self._run()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(calls)
        egress = calls[0]["system"] + calls[0]["prompt"]
        self.assertNotIn("Mohammed", egress)
        self.assertNotIn("Alqahtani", egress)
        self.assertNotIn("mohammed.q@customer.example", egress)
        self.assertNotIn("966501234567", egress)
        self.assertIn("partner_ref", calls[0]["prompt"],
                      "the profile rides under an opaque reference")
        # ... and the PII is merged LOCALLY after the LLM returned.
        line = self.Line.search([("partner_id", "=", partner.id)])
        self.assertIn("Mohammed Alqahtani", line.generated_body)

    # 5 ------------------------------------------------------------------
    def test_05_cooldown_respected(self):
        partner = self._partner("Recently Mailed", "cool@customer.example")
        self._set("monthly_frequency_cap", "0")  # isolate the cooldown gate
        self._sent_line(partner, fields.Datetime.now() - timedelta(days=5))
        result, _calls = self._run()
        line = self.Line.search([("partner_id", "=", partner.id),
                                 ("state", "=", "skipped")])
        self.assertEqual(line.skip_reason, "cooldown")
        self.assertEqual(result["status"], "no_eligible_partners")

    # 6 ------------------------------------------------------------------
    def test_06_monthly_frequency_cap_across_campaigns(self):
        partner = self._partner("Capped Partner", "cap@customer.example")
        self._set("cooldown_days", "0")  # isolate the monthly cap gate
        now = fields.Datetime.now().replace(day=1) + timedelta(days=1)
        self._sent_line(partner, now)          # campaign A this month
        self._sent_line(partner, now)          # campaign B this month
        result, _calls = self._run()
        line = self.Line.search([("partner_id", "=", partner.id),
                                 ("state", "=", "skipped")])
        self.assertEqual(line.skip_reason, "monthly_cap")
        self.assertEqual(result["status"], "no_eligible_partners")

    # 7 ------------------------------------------------------------------
    def test_07_suppression_list_excludes(self):
        by_partner = self._partner("Suppressed Person", "sp@keep.example")
        by_domain = self._partner("Domain Suppressed", "x@blocked.example")
        self.Suppression.create({"partner_id": by_partner.id,
                                 "reason": "requested"})
        self.Suppression.create({"email_pattern": "blocked.example",
                                 "reason": "complaints"})
        result, _calls = self._run()
        self.assertEqual(result["status"], "no_eligible_partners")
        for partner in (by_partner, by_domain):
            line = self.Line.search([("partner_id", "=", partner.id)])
            self.assertEqual(line.state, "skipped")
            self.assertEqual(line.skip_reason, "suppressed")

    # 8 ------------------------------------------------------------------
    def test_08_send_window_and_blackout_defer(self):
        self._partner("Anyone", "any@customer.example")
        # Closed window: zero-width.
        self._set("send_window_start", "0.0")
        self._set("send_window_end", "0.0")
        result, calls = self._run(unattended=True)
        self.assertEqual(result["status"], "deferred")
        self.assertFalse(self.Campaign.search([]))
        self.assertFalse(calls)
        self.assertFalse(self.Mailing.search([]))
        # Blackout on today's weekday, window wide open again.
        self._set("send_window_end", "24.0")
        codes = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        today = codes[fields.Datetime.now().weekday()]
        self._set("blackout_days", today)
        result, calls = self._run(unattended=True)
        self.assertEqual(result["status"], "deferred")
        self.assertFalse(self.Campaign.search([]))
        self.assertFalse(calls)

    # 9 ------------------------------------------------------------------
    def test_09_daily_limit_and_max_campaigns(self):
        for i in range(7):
            self._partner("Bulk %d" % i, "bulk%d@customer.example" % i)
        self._set("daily_limit", "4")
        self._set("max_daily_campaigns", "2")
        result, calls = self._run()
        self.assertEqual(result["status"], "ok")
        targeted = self.Line.search([("state", "!=", "skipped")])
        self.assertEqual(len(targeted), 4, "daily limit caps targeting")
        self.assertEqual(len(calls), 4)
        self.assertLessEqual(len(self.Campaign.search([])), 2,
                             "max campaigns/day respected")
        # A second run the same day may not exceed the day budget either.
        result2, calls2 = self._run()
        self.assertEqual(result2["status"], "daily_limits_reached")
        self.assertFalse(calls2)

    # 10 -----------------------------------------------------------------
    def test_10_llm_daily_call_cap_halts_generation(self):
        for i in range(4):
            self._partner("Cap %d" % i, "llmcap%d@customer.example" % i)
        self._set("llm_daily_call_cap", "2")
        result, calls = self._run()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 2, "generation halts AT the cap")
        halted = self.Line.search([("skip_reason", "=", "llm_cap_reached")])
        self.assertEqual(len(halted), 2)

    # 11 -----------------------------------------------------------------
    def test_11_low_confidence_forces_review(self):
        self._approver()
        self._set("require_human_approval", "False")
        self._partner("Uncertain Match", "low@customer.example")
        result, _calls = self._run(confidence=0.4)
        self.assertEqual(result["pending_approval"], 1)
        campaign = self.Campaign.search([("state", "=", "pending_approval")])
        self.assertTrue(campaign,
                        "a below-threshold line forces human review even "
                        "with the approval toggle off")
        self.assertFalse(self.Mailing.search([]))

    # 12 -----------------------------------------------------------------
    def test_12_approval_required_empty_approvers_fails_closed(self):
        self._set("require_human_approval", "True")
        self._set("approver_user_ids", " ")
        self._partner("Never Reached", "never@customer.example")
        result, calls = self._run()
        self.assertEqual(result["status"], "no_approvers")
        campaign = self.Campaign.browse(result["campaign_ids"])
        self.assertEqual(campaign.state, "failed")
        self.assertFalse(calls, "fail-closed BEFORE any LLM spend")
        self.assertFalse(self.Mailing.search([]))
        self.assertFalse(self.env["mail.mail"].search(
            [("mailing_id", "!=", False)]))

    # 13 -----------------------------------------------------------------
    def test_13_approver_approves_then_mailing_created(self):
        approver = self._approver()
        self._set("require_human_approval", "True")
        partner = self._partner("Approved Customer", "ok@customer.example")
        result, _calls = self._run()
        self.assertEqual(result["pending_approval"], 1)
        campaign = self.Campaign.browse(result["campaign_ids"]).filtered(
            lambda c: c.state == "pending_approval")
        self.assertTrue(campaign)
        self.assertFalse(self.Mailing.search([]), "nothing sent pre-approval")
        campaign.with_user(approver).action_approve()
        self.assertEqual(campaign.state, "sent")
        self.assertTrue(campaign.mailing_id, "official mailing.mailing set")
        line = self.Line.search([("partner_id", "=", partner.id)])
        self.assertEqual(line.state, "sent")
        self.assertTrue(line.sent_date)
        mails = self.env["mail.mail"].search(
            [("mailing_id", "=", campaign.mailing_id.id)])
        self.assertEqual(len(mails), 1)
        self.assertIn("Approved Customer", mails.body_html,
                      "PII merged locally into the outgoing body")

    # 14 -----------------------------------------------------------------
    def test_14_auto_handoff_without_approval(self):
        self._set("require_human_approval", "False")
        partner = self._partner("Auto Sent", "auto@customer.example")
        result, _calls = self._run(confidence=0.95)
        self.assertEqual(result["sent"], 1)
        campaign = self.Campaign.search([("state", "=", "sent")])
        self.assertTrue(campaign.mailing_id)
        self.assertEqual(campaign.mailing_id.mailing_model_id.model,
                         "res.partner")
        line = self.Line.search([("partner_id", "=", partner.id)])
        self.assertEqual(line.state, "sent")
        self.assertEqual(line.matched_service_id, self.service)

    # 15 -----------------------------------------------------------------
    def test_15_hallucinated_service_rejected(self):
        self._partner("Grounded", "ground@customer.example")
        bogus = json.dumps({
            "service_id": 99999999,
            "subject": "Fake", "body": "<p>Fake</p>",
            "match_confidence": 0.99,
        })
        result, calls = self._run(reply=bogus)
        self.assertTrue(calls)
        line = self.Line.search([("skip_reason", "=", "invalid_service")])
        self.assertEqual(len(line), 1)
        self.assertEqual(line.state, "skipped")
        self.assertFalse(line.matched_service_id)
        self.assertFalse(self.Mailing.search([]),
                         "a hallucinated service never reaches a send")
        self.assertEqual(result["failed"], 1)

    # 16 -----------------------------------------------------------------
    def test_16_playbook_instruction_injected(self):
        tag = self.env["res.partner.category"].create(
            {"name": "new_customer"})
        playbook = self.Playbook.create({
            "name": "New to Odoo",
            "trigger_tag_ids": [(6, 0, tag.ids)],
            "instruction": "Recipient is new to Odoo; open with a short "
                           "intro to what Odoo is before presenting the "
                           "service.",
        })
        tagged_p = self._partner("Newbie", "new@customer.example",
                                 tags=tag.ids)
        plain_p = self._partner("Regular", "reg@customer.example")
        _result, calls = self._run()
        self.assertEqual(len(calls), 2)
        tagged_line = self.Line.search([("partner_id", "=", tagged_p.id)])
        plain_line = self.Line.search([("partner_id", "=", plain_p.id)])
        self.assertIn(playbook, tagged_line.applied_playbook_ids)
        self.assertNotIn(playbook, plain_line.applied_playbook_ids)
        tagged_call = next(c for c in calls
                           if "line:%d" % tagged_line.id in c["prompt"])
        plain_call = next(c for c in calls
                          if "line:%d" % plain_line.id in c["prompt"])
        self.assertIn("new to Odoo", tagged_call["prompt"])
        self.assertNotIn("new to Odoo", plain_call["prompt"])

    # 17 -----------------------------------------------------------------
    def test_17_era_ai_accounts_transport_absent_fails_closed(self):
        self._set("llm_transport", "era_ai_accounts")
        self._partner("Waiting", "wait@customer.example")
        AgentCls = type(self.agent_model)
        with mock.patch.object(AgentCls, "_era_ai_accounts_available",
                               return_value=False):
            result, calls = self._run(unattended=True)
        self.assertEqual(result["status"], "transport_unavailable")
        self.assertEqual(result["reason"], "era_ai_accounts_unavailable")
        self.assertFalse(calls, "fail closed: no LLM call on any other path")
        self.assertFalse(self.Campaign.search([]))
        self.assertFalse(self.Mailing.search([]))
        audit = self.env["crm.ai.audit.log"].search(
            [("event_type", "=", "blocked")], order="id desc", limit=1)
        self.assertTrue(audit, "the fail-closed decision is audited")

    # ------------------------------------------------------------------
    # Success-path audit rows (19.0.1.0.1)
    # ------------------------------------------------------------------
    def _campaign_audit(self, event_type):
        return self.env["crm.ai.audit.log"].sudo().search([
            ("agent_id.tech_name", "=", "era_campaign"),
            ("event_type", "=", event_type),
        ])

    def test_18_pending_approval_writes_one_audit_row(self):
        self._approver()
        self._set("require_human_approval", "True")
        self._partner("Routed Customer", "routed@customer.example")
        result, _calls = self._run()
        self.assertEqual(result["pending_approval"], 1)
        rows = self._campaign_audit("approval_requested")
        self.assertEqual(len(rows), 1,
                         "exactly one pending-approval audit row per campaign")
        payload = rows.value_after or ""
        self.assertIn("campaign_pending_approval", payload)
        self.assertIn('"partner_count": 1', payload)
        self.assertNotIn("Routed Customer", payload, "no PII in audit")
        self.assertNotIn("routed@customer.example", payload)

    def test_19_consent_denial_writes_one_audit_row(self):
        if "crm.ai.consent" in self.env:
            self.skipTest("Compliance installed — clean-DB denial test.")
        self._set("pdpl_guard_enabled", "True")
        partner = self._partner("Denied Customer", "denied@customer.example")
        self._run()
        rows = self._campaign_audit("blocked").filtered(
            lambda r: "consent_denied" in (r.value_after or ""))
        self.assertEqual(len(rows), 1,
                         "exactly one denial audit row for the partner")
        payload = rows.value_after or ""
        self.assertIn('"partner_id": %d' % partner.id, payload)
        self.assertIn("no_consent_guard", payload)
        self.assertNotIn("Denied Customer", payload, "no PII in audit")
        self.assertNotIn("denied@customer.example", payload)

    def test_20_audit_write_failure_is_swallowed(self):
        self._partner("Survives Logging", "survive@customer.example")
        LogCls = type(self.env["crm.ai.audit.log"])
        with mock.patch.object(LogCls, "log",
                               side_effect=RuntimeError("boom")):
            result, calls = self._run()
        self.assertEqual(result["status"], "ok",
                         "an audit-write failure must not abort the run")
        self.assertEqual(result["sent"], 1)
        self.assertTrue(calls)
        campaign = self.Campaign.search([("state", "=", "sent")])
        self.assertTrue(campaign.mailing_id,
                        "the run completed through hand-off despite the "
                        "audit failure")

    # ------------------------------------------------------------------
    # Transport default (19.0.1.0.2): era_ai_accounts default, token intact
    # ------------------------------------------------------------------
    def test_21_default_transport_is_era_ai_accounts(self):
        from odoo.addons.era_crm_ai_agents_campaign.services.campaign_engine import (
            CampaignEngine,
        )
        # Remove the harness's stored value so we observe the INSTALL default
        # (config_parameter-bound fields read the stored param first).
        self.Param.search([
            ("key", "=", "era_crm_ai_agents_campaign.llm_transport")
        ]).unlink()
        # (a) A fresh settings form defaults to the subscription transport.
        defaults = self.env["res.config.settings"].default_get(
            ["campaign_llm_transport"])
        self.assertEqual(defaults["campaign_llm_transport"],
                         "era_ai_accounts")
        # (b) The engine resolves the same default with no stored param —
        # and fails closed while the CLI path is unconfigured.
        ok, reason = CampaignEngine(self.agent_model)._transport_gate()
        self.assertFalse(ok)
        self.assertEqual(reason, "era_ai_accounts_unavailable")

    def test_22_token_transport_still_selectable_and_routes(self):
        from odoo.addons.era_crm_ai_agents_campaign.services.campaign_engine import (
            CampaignEngine,
        )
        self._set("llm_transport", "token")
        ok, reason = CampaignEngine(self.agent_model)._transport_gate()
        self.assertTrue(ok, "the token branch must remain fully usable")
        self._partner("Token Path", "token@customer.example")
        result, calls = self._run()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 1,
                         "generation ran over the token (api) branch")
