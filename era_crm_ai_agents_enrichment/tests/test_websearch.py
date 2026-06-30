# -*- coding: utf-8 -*-
"""Unit tests for the web-search provider handler (CLI-subscription path).

Fully OFFLINE: the LLM call (``crm.ai.enrichment.agent._call_llm``) is patched, so
no CLI subprocess or network is touched. We exercise the handler's own logic: the
fail-closed toggle, the company-name resolution / documented skip, strict JSON
parsing, and the no-fabricated-cost (``billable=False``) contract.
"""
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from ..services.handlers.websearch import WebSearchHandler

_NS = "era_crm_ai_agents_enrichment."
_AGENT = "crm.ai.enrichment.agent"


@tagged("post_install", "-at_install")
class TestWebSearchHandler(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.provider = cls.env["crm.ai.enrichment.provider"].create({
            "name": "Web Search (test)",
            "provider_type": "web_search",
            "handler_key": "websearch",
            "fields_filled": "name,website,sector,city,country",
            "priority": 90,
            "billing_mode": "free",
            "active": True,
        })

    def _handler(self):
        return WebSearchHandler(self.env, self.provider)

    def _set_toggle(self, on):
        self.ICP.set_param(_NS + "websearch_provider_enabled", "True" if on else "False")

    # -- 1) fail-closed toggle ------------------------------------------------
    def test_toggle_off_not_available(self):
        """Toggle OFF (and unset) -> is_available() False, no exception (skip)."""
        self.ICP.set_param(_NS + "websearch_provider_enabled", "False")
        self.assertFalse(self._handler().is_available())
        # Unset param is also treated as OFF (fail-closed).
        self.env["ir.config_parameter"].search(
            [("key", "=", _NS + "websearch_provider_enabled")]).unlink()
        self.assertFalse(self._handler().is_available())

    def test_toggle_on_available(self):
        self._set_toggle(True)
        self.assertTrue(self._handler().is_available())

    # -- 2) documented skip when there is no company name --------------------
    def test_no_company_name_documented_skip(self):
        """An individual with no company parent -> no_data, billable False, no
        crash and the LLM is never even called."""
        self._set_toggle(True)
        person = self.env["res.partner"].create({
            "name": "Standalone Person", "is_company": False})
        with patch.object(type(self.env[_AGENT]), "_call_llm") as mock_llm:
            res = self._handler().enrich(person, ["website", "city"])
        self.assertEqual(res["status"], "no_data")
        self.assertFalse(res["billable"])
        self.assertEqual(res["data"], {})
        mock_llm.assert_not_called()

    # -- 3) happy path: structured JSON is parsed into data ------------------
    def test_company_success_parses_json(self):
        self._set_toggle(True)
        company = self.env["res.partner"].create({
            "name": "Acme Industrial Co", "is_company": True})
        reply = ['```json\n{"website": "https://acme.example", '
                 '"city": "Riyadh", "sector": null}\n```']
        with patch.object(type(self.env[_AGENT]), "_call_llm",
                          return_value=reply) as mock_llm:
            res = self._handler().enrich(company, ["website", "city", "sector"])
        # Called exactly once; record NOT passed (PDPL — public company name only).
        mock_llm.assert_called_once()
        _, kwargs = mock_llm.call_args
        self.assertIsNone(kwargs.get("record"))
        self.assertEqual(kwargs.get("sensitivity"), "low")
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["billable"])          # subscription -> no dollar cost
        self.assertEqual(res["data"], {"website": "https://acme.example",
                                       "city": "Riyadh"})  # null sector dropped

    # -- 4) malformed model output -> no corrupt write -----------------------
    def test_unparseable_output_no_data(self):
        self._set_toggle(True)
        company = self.env["res.partner"].create({
            "name": "Acme Industrial Co", "is_company": True})
        with patch.object(type(self.env[_AGENT]), "_call_llm",
                          return_value=["sorry, I could not find anything useful"]):
            res = self._handler().enrich(company, ["website", "city"])
        self.assertEqual(res["status"], "no_data")
        self.assertEqual(res["data"], {})
        self.assertFalse(res["billable"])

    # -- 5) LLM raising never kills the run ----------------------------------
    def test_llm_exception_returns_error(self):
        self._set_toggle(True)
        company = self.env["res.partner"].create({
            "name": "Acme Industrial Co", "is_company": True})
        with patch.object(type(self.env[_AGENT]), "_call_llm",
                          side_effect=RuntimeError("boom")):
            res = self._handler().enrich(company, ["website"])
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["data"], {})
        self.assertFalse(res["billable"])
