# -*- coding: utf-8 -*-
"""Configurable-protection-layer tests (Change 1).

Proves the operational/compliance toggles change guard behaviour, and that a
disabled compliance layer warns loudly on every call.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.ai.utils.llm_api_service import LLMApiService
from odoo.addons.era_crm_ai_agents_base.services import ai_compliance_guard as guard

_LOGGER = "odoo.addons.era_crm_ai_agents_base.services.ai_compliance_guard"


@tagged("post_install", "-at_install")
class TestGuardToggles(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env["crm.ai.agent"].sudo().create(
            {"name": "Toggle Test", "tech_name": "era_toggle_test"})

    def _svc(self, **ctx):
        return LLMApiService(
            env=self.env(context=dict(self.env.context, **ctx)), provider="openai")

    def _set(self, **params):
        icp = self.env["ir.config_parameter"].sudo()
        for k, v in params.items():
            icp.set_param("era_crm_ai_agents." + k, v)

    def test_redaction_off_sends_unmasked_and_warns(self):
        self._set(enable_pii_redaction="False")
        partner = self.env["res.partner"].create({
            "name": "Redact Off", "phone": "+966500000002",
            "crm_ai_intl_processing_consent": True})
        sent = {}

        def fake_call(masked_system, masked_user):
            sent["user"] = masked_user
            return ["ok"]

        svc = self._svc(**{guard.CTX_AGENT: "era_toggle_test",
                           guard.CTX_RECORD: partner})
        with self.assertLogs(_LOGGER, level="WARNING") as cm:
            guard._guard_text(svc, fake_call, "gpt-4o", [], ["ring +966500000002"])
        # Redaction OFF -> the real phone went out unmasked:
        self.assertIn("+966500000002", sent["user"][0])
        # ...and every such call warns loudly:
        self.assertTrue(any("enable_pii_redaction is DISABLED" in m for m in cm.output))

    def test_consent_off_does_not_block(self):
        self._set(enable_consent_check="False")
        partner = self.env["res.partner"].create({
            "name": "No Consent", "phone": "+966500000003",
            "crm_ai_intl_processing_consent": False})  # NOT consented
        sent = {"hit": False}

        def fake_call(masked_system, masked_user):
            sent["hit"] = True
            return ["[[PII:PHONE:1]]"]

        svc = self._svc(**{guard.CTX_AGENT: "era_toggle_test",
                           guard.CTX_RECORD: partner})
        out = guard._guard_text(svc, fake_call, "gpt-4o", [], ["ring +966500000003"])
        # Consent OFF -> a non-consented partner's call is NOT blocked:
        self.assertTrue(sent["hit"])
        self.assertIn("+966500000003", out[0])  # redaction still on -> restored
