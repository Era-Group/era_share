# -*- coding: utf-8 -*-
"""MANDATORY upgrade smoke test for the AI Compliance Guard.

The guard monkeypatches four native Odoo methods. Those are plain-class /
EE-internal methods with NO stability contract, so an Odoo upgrade that renames
or reorders a parameter would make our wrappers silently wrong — disabling PDPL
redaction / the cost cap without any error.

This test pins the native signatures captured at patch time against the frozen
expectation below. If Odoo changes any patched method, this test FAILS on every
``-u`` / CI run, forcing us to review the guard before shipping. It also asserts
the guard is actually installed.

Run: ``odoo -u era_crm_ai_agents_base --test-enable --stop-after-init``
"""
import inspect

from odoo.tests import TransactionCase, tagged

from odoo.addons.ai.utils.llm_api_service import LLMApiService
from odoo.addons.ai.models.ir_actions_server import IrActionsServer
from odoo.addons.era_crm_ai_agents_base.services import ai_compliance_guard as guard

# Frozen expectation — taken from the verified Odoo 19 source. Update ONLY after
# deliberately reviewing the guard against the new Odoo version.
EXPECTED = {
    "request_llm": ["self", "llm_model", "system_prompts", "user_prompts",
                    "tools", "files", "schema", "temperature", "inputs",
                    "web_grounding"],
    "get_embedding": ["self", "input", "dimensions", "model",
                      "encoding_format", "user"],
    "get_transcription": ["self", "data", "mimetype", "model", "language",
                          "prompt", "response_format", "temperature"],
    "_ai_action_run": ["self", "record"],
}


@tagged("post_install", "-at_install")
class TestNativeAiSignatures(TransactionCase):

    def test_guard_is_installed(self):
        """The monkeypatch must be active, or nothing is being enforced."""
        self.assertTrue(
            getattr(LLMApiService, guard._SENTINEL, False),
            "AI Compliance Guard is NOT installed — native AI is unguarded.",
        )

    def test_captured_native_signatures_match_expected(self):
        """Native signatures captured at patch time must match the frozen spec.

        A mismatch means Odoo changed a method the guard wraps — review the guard
        (services/ai_compliance_guard.py) before updating EXPECTED.
        """
        self.assertTrue(guard.ORIGINAL_SIGNATURES,
                        "No native signatures captured — install() did not run.")
        for name, expected in EXPECTED.items():
            self.assertEqual(
                guard.ORIGINAL_SIGNATURES.get(name), expected,
                "Native signature drift for %s — Odoo changed it. Review the "
                "guard wrapper before updating EXPECTED." % name,
            )

    def test_our_wrappers_match_expected(self):
        """Our installed wrappers must keep the same public signature too."""
        live = {
            "request_llm": list(inspect.signature(LLMApiService.request_llm).parameters),
            "get_embedding": list(inspect.signature(LLMApiService.get_embedding).parameters),
            "get_transcription": list(inspect.signature(LLMApiService.get_transcription).parameters),
            "_ai_action_run": list(inspect.signature(IrActionsServer._ai_action_run).parameters),
        }
        for name, expected in EXPECTED.items():
            self.assertEqual(
                live[name], expected,
                "Guard wrapper signature for %s drifted from the native spec." % name,
            )
