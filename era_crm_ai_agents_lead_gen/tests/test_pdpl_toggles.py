# -*- coding: utf-8 -*-
"""PDPL gating (16.4 / 16.7): inert-when-disabled + decision-maker double-gate."""
from odoo.tests import tagged

from .common import LeadGenCase, COMPANY_JSON, CONTACT_JSON


@tagged("post_install", "-at_install")
class TestPdplToggles(LeadGenCase):

    def test_inert_when_disabled(self):
        """master toggle OFF => zero source calls, zero LLM calls, nothing made."""
        self._set("enabled", "False")
        self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        before = self.Partner.search_count([("x_lead_gen_source", "!=", False)])
        result, http = self._run("<<raw>>")
        self.assertFalse(result["enabled"])
        self.assertEqual(http.call_count, 0, "no source call when disabled")
        self.assertEqual(
            self.Partner.search_count([("x_lead_gen_source", "!=", False)]), before)

    def test_decision_makers_need_both_toggles(self):
        """A decision-maker source is fetched ONLY when both the master toggle and
        fetch_decision_makers are ON."""
        # web_search type (GET handler) but decision_maker category, so the
        # handler runs while the PDPL category gate is what's under test.
        self._provider("People Source", 10, "ERA_LEADGEN_SERPAPI_DM",
                       category="decision_maker")

        # enabled=True (setUp) but fetch_decision_makers OFF -> no contacts.
        self._set("fetch_decision_makers", "False")
        result, http = self._run("<<raw>>", llm_json=CONTACT_JSON)
        self.assertEqual(result["contacts_created"], 0)
        self.assertEqual(http.call_count, 0,
                         "decision-maker source must not even be called")

        # Now BOTH on -> the decision-maker source is fetched and a contact made.
        self._set("fetch_decision_makers", "True")
        result, http = self._run("<<raw>>", llm_json=CONTACT_JSON)
        self.assertGreaterEqual(result["contacts_created"], 1)
        self.assertTrue(self.Partner.search([
            ("name", "=", "Jane Boss"), ("is_company", "=", False)]))
