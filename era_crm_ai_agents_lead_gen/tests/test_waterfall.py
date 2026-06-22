# -*- coding: utf-8 -*-
"""Waterfall behaviour + cost (16.4 / 16.5 / 16.6) + cap/unattended guards."""
from unittest import mock

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import LeadGenCase, COMPANY_JSON


@tagged("post_install", "-at_install")
class TestWaterfall(LeadGenCase):

    def test_fail_then_success_stops_at_first(self):
        """First source returns nothing -> waterfall falls to the next, which
        succeeds, and then STOPS (no third call)."""
        self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        self._provider("Search B", 20, "ERA_LEADGEN_SERPAPI_B")
        self._provider("Search C", 30, "ERA_LEADGEN_SERPAPI_C")
        # A fails (None), B succeeds (raw), C must never be reached.
        result, http = self._run([None, "<<raw from B>>"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["companies_created"], 1,
                         "exactly the one company from B should be created")
        # Two fetches only: A (fail) then B (success); C skipped by the stop.
        self.assertEqual(http.call_count, 2,
                         "waterfall must stop at first success, not try C")
        self.assertTrue(
            self.Partner.search([("name", "=", "Acme Corp"),
                                 ("is_company", "=", True)]))

    def test_cost_counts_source_and_llm_batch_level(self):
        """A successful batch records BOTH halves of the spend at batch level:
        one source_api usage row + the guard's llm row (distinguished by
        usage_type). No per-record splitting."""
        self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        self._run("<<raw>>")
        agent = self.env["crm.ai.lead_gen.agent"]._get_agent_record()
        types = set(self.Usage.search([("agent_id", "=", agent.id)]).mapped("usage_type"))
        self.assertEqual(types, {"source_api", "llm"},
                         "both source_api and llm cost halves must be recorded")

    def test_cost_cap_blocks_source_call_midrun(self):
        """Once the monthly $/token limit is blown, the per-source pre-check stops
        the waterfall BEFORE any source-API call (zero further spend)."""
        from odoo.addons.era_crm_ai_agents_lead_gen.services.lead_gen_engine import (
            LeadGenEngine,
        )
        prov = self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        agent = self.env["crm.ai.lead_gen.agent"]._get_agent_record()
        self.Usage.record(agent, False, 0, 0, 9999.0)  # blow the $100 monthly cap
        engine = LeadGenEngine(self.env["crm.ai.lead_gen.agent"])
        AgentCls = type(self.agent_model)
        with mock.patch.dict("os.environ", {"ERA_LEADGEN_SERPAPI_A": "tok"}), \
                mock.patch.object(AgentCls, "_http_get", autospec=True,
                                  return_value="<<raw>>") as http:
            res = engine._waterfall(prov, kind="company", target=engine._target())
        self.assertTrue(res["capped"])
        self.assertEqual(http.call_count, 0,
                         "no source call once the monthly cap is blown")

    def test_unattended_skips_when_over_cap_no_raise(self):
        """A cron (unattended) run over the cap must skip-with-audit, NOT raise."""
        self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        agent = self.env["crm.ai.lead_gen.agent"]._get_agent_record()
        self.Usage.record(agent, False, 0, 0, 9999.0)
        before = self.Audit.search_count([("event_type", "=", "cost_cap_exceeded")])
        result, http = self._run("<<raw>>", unattended=True)
        self.assertTrue(result.get("capped"))
        self.assertEqual(http.call_count, 0)
        self.assertGreater(
            self.Audit.search_count([("event_type", "=", "cost_cap_exceeded")]), before,
            "the skip must be audited")

    def test_attended_raises_when_over_cap(self):
        """An interactive (attended) run over the cap fails fast (UserError)."""
        self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        agent = self.env["crm.ai.lead_gen.agent"]._get_agent_record()
        self.Usage.record(agent, False, 0, 0, 9999.0)
        with self.assertRaises(UserError):
            self._run("<<raw>>")  # unattended defaults to False

    def test_daily_cap_blocks_creation(self):
        """With the cap already reached, a scheduled/manual run creates nothing
        and audits the cap hit (no silent overspend)."""
        self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        # Cap of 1, already met by one record created today -> run is blocked.
        self._set("daily_cap", "1")
        self.Partner.create({"name": "Seeded LG", "is_company": True,
                             "x_lead_gen_source": "seed"})
        before = self.Partner.search_count([("x_lead_gen_source", "!=", False)])
        result, _ = self._run("<<raw>>")
        after = self.Partner.search_count([("x_lead_gen_source", "!=", False)])
        self.assertEqual(after, before, "cap reached -> no new records created")
        self.assertTrue(result["capped"])
