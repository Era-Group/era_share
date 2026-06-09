# -*- coding: utf-8 -*-
"""Unpriced-model fail-safe tests (Change 2 / Rule 14).

A model with no rate-card price must NOT silently cost 0: by default the call is
blocked; when the manager opts to allow it, the usage row is flagged ``unpriced``
(cost 0) so it surfaces on the dashboard for a price to be added.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ai.utils.llm_api_service import LLMApiService
from odoo.addons.era_crm_ai_agents_base.services import ai_compliance_guard as guard


@tagged("post_install", "-at_install")
class TestUnpricedModel(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env["crm.ai.agent"].sudo().create(
            {"name": "Unpriced Test", "tech_name": "era_unpriced_test"})

    def _svc(self, **ctx):
        return LLMApiService(
            env=self.env(context=dict(self.env.context, **ctx)), provider="openai")

    def test_unpriced_blocks_by_default(self):
        sent = {"hit": False}

        def fake_call(masked_system, masked_user):
            sent["hit"] = True
            return ["x"]

        svc = self._svc(**{guard.CTX_AGENT: "era_unpriced_test"})
        with self.assertRaises(UserError):
            guard._guard_text(svc, fake_call, "some-unpriced-model", ["s"], ["hi"])
        self.assertFalse(sent["hit"], "unpriced model must block before any spend")

    def test_unpriced_allowed_when_configured_flags_usage(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "era_crm_ai_agents.block_unpriced_model", "False")

        def fake_call(masked_system, masked_user):
            return ["x"]

        svc = self._svc(**{guard.CTX_AGENT: "era_unpriced_test"})
        out = guard._guard_text(svc, fake_call, "some-unpriced-model", ["s"], ["hi"])
        self.assertEqual(out, ["x"])
        usage = self.env["crm.ai.usage"].sudo().search(
            [("agent_id.tech_name", "=", "era_unpriced_test")], order="id desc", limit=1)
        self.assertTrue(usage.unpriced)
        self.assertEqual(usage.cost, 0.0)
