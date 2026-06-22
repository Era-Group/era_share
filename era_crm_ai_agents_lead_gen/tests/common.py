# -*- coding: utf-8 -*-
"""Shared hermetic harness for the engine tests.

Keeps every test offline: the agent's external egress (``_http_get``) and LLM
call (``_call_llm``) are patched at the model-class level. The LLM stub also
books a stub ``llm`` usage row, exactly as the live guard would, so cost
attribution (source + LLM per record) is genuinely exercised — no native AI, no
network, no Claude CLI.
"""
import json
from unittest import mock

from odoo.tests import TransactionCase

COMPANY_JSON = json.dumps([
    {"name": "Acme Corp", "website": "acme.example", "email": "info@acme.example"}])
CONTACT_JSON = json.dumps([
    {"name": "Jane Boss", "job_title": "CEO", "email": "jane@acme.example",
     "company": "Acme Corp"}])


class LeadGenCase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Provider = self.env["crm.ai.lead_gen.provider"]
        self.Param = self.env["ir.config_parameter"].sudo()
        self.Usage = self.env["crm.ai.usage"]
        self.Partner = self.env["res.partner"]
        self.Audit = self.env["crm.ai.audit.log"]
        self.agent_model = self.env["crm.ai.lead_gen.agent"]
        # Deterministic waterfalls: drop the seeded sources.
        self.Provider.with_context(active_test=False).search([]).unlink()
        # Engine tests run with the module ON unless a test turns it off.
        self._set("enabled", "True")
        self._set("daily_cap", "1000")  # generous; cap tests set their own

    def _set(self, key, val):
        self.Param.set_param("era_crm_ai_agents_lead_gen." + key, val)

    def _provider(self, name, priority, env_key, category="company",
                  ptype="web_search", active=True):
        return self.Provider.create({
            "name": name, "priority": priority, "provider_type": ptype,
            "category": category, "env_key_param": env_key, "active": active,
            "cost": 0.02,
        })

    def _run(self, http_returns, llm_json=COMPANY_JSON, extra_env=None, **kw):
        """Patch egress + LLM, then run one pass. Returns (result, http_mock).

        :param http_returns: a single value (every fetch returns it) or a list
            (consumed in order across fetches — model a fail-then-success).
        """
        AgentCls = type(self.agent_model)

        # Make every active source's token resolve, unless a test overrides.
        env = {p.env_key_param: "tok"
               for p in self.Provider.search([("active", "=", True)])
               if p.env_key_param}
        env.update(extra_env or {})

        def fake_call_llm(self_, prompt, sensitivity="low", system=None,
                          record=None, unattended=False, max_output_tokens=1024):
            # Simulate the guard booking the LLM usage row for this call.
            ag = self_._get_agent_record()
            self_.env["crm.ai.usage"].record(
                ag, False, 100, 50, 0.01, usage_type="llm")
            return [llm_json]

        http_kw = ({"side_effect": http_returns}
                   if isinstance(http_returns, list)
                   else {"return_value": http_returns})

        with mock.patch.dict("os.environ", env), \
                mock.patch.object(AgentCls, "_http_get", autospec=True, **http_kw) as http, \
                mock.patch.object(AgentCls, "_call_llm", new=fake_call_llm):
            result = self.agent_model.run_lead_generation(**kw)
        return result, http
