# -*- coding: utf-8 -*-
"""Scope-gate tests (Option C — hybrid enforcement).

Proves the security-relevant branch behaves differently per origin:
  * OUR agents (CTX_AGENT set)  -> hard-block on unmapped PII (never sent).
  * native AI  (no CTX_AGENT)   -> redacts known record PII but does NOT block,
    so Odoo's own Ask-AI / ai_fields keep working.

The native LLM transport is never hit: we drive the pipeline with a fake ``call``
callable and assert whether it was invoked and what masked text it received.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ai.utils.llm_api_service import LLMApiService
from odoo.addons.era_crm_ai_agents_base.services import ai_compliance_guard as guard


@tagged("post_install", "-at_install")
class TestGuardScope(TransactionCase):

    def _service(self, **ctx):
        return LLMApiService(
            env=self.env(context=dict(self.env.context, **ctx)), provider="openai"
        )

    def test_our_agent_hard_blocks_unmapped_pii(self):
        """CTX_AGENT set + a phone not tied to a record -> hard block, nothing sent."""
        self.env["crm.ai.agent"].sudo().create(
            {"name": "Scope Test", "tech_name": "era_scope_test"})
        sent = {"hit": False}

        def fake_call(masked_system, masked_user):
            sent["hit"] = True
            return ["should not be reached"]

        svc = self._service(**{guard.CTX_AGENT: "era_scope_test"})
        with self.assertRaises(UserError):
            guard._guard_text(svc, fake_call, "gpt-4o",
                              ["system"], ["call me at 0559876543"])
        self.assertFalse(sent["hit"], "outbound call must NOT happen on a block")

    def test_native_path_does_not_block(self):
        """No CTX_AGENT -> native path: unmapped PII does NOT block the call."""
        sent = {"hit": False, "user": None}

        def fake_call(masked_system, masked_user):
            sent["hit"] = True
            sent["user"] = masked_user
            return ["ok native"]

        svc = self._service()  # native: no CTX_AGENT
        out = guard._guard_text(svc, fake_call, "gpt-4o",
                                ["system"], ["call me at 0559876543"])
        self.assertTrue(sent["hit"], "native path must NOT block")
        self.assertEqual(out, ["ok native"])

    def test_native_path_still_redacts_known_record_pii(self):
        """Native path is best-effort: known record PII is still masked before egress."""
        partner = self.env["res.partner"].create(
            {"name": "Native Test", "phone": "+966500000001"})
        sent = {"user": None}

        def fake_call(masked_system, masked_user):
            sent["user"] = masked_user
            return ["[[PII:PHONE:1]] noted"]

        svc = self._service(**{guard.CTX_RECORD: partner})  # record, but NO CTX_AGENT
        out = guard._guard_text(svc, fake_call, "gpt-4o", [], ["ring +966500000001"])
        # Known record PII masked before it left:
        self.assertNotIn("+966500000001", sent["user"][0])
        self.assertIn("[[PII:PHONE", sent["user"][0])
        # Best-effort re-insertion restored it on the way back (no raise):
        self.assertIn("+966500000001", out[0])
