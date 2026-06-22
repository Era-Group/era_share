# -*- coding: utf-8 -*-
"""Provider model: orderability, token-presence, and the never-silent skip.

Proves 16.1's contract: providers are orderable by priority; token_present is
True only when the env var the provider names resolves; and an active source
with a MISSING token is skipped but NEVER silently — a `blocked` audit row is
written (the warn policy, default).
"""
from unittest import mock

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProvider(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Provider = self.env["crm.ai.lead_gen.provider"]
        # Start from a clean slate so seeded rows don't perturb counts/order.
        self.Provider.with_context(active_test=False).search([]).unlink()

    def _make(self, name, priority, env_key="", active=True, category="company"):
        return self.Provider.create({
            "name": name, "priority": priority, "provider_type": "web_search",
            "category": category, "env_key_param": env_key, "active": active,
        })

    def test_orderable_by_priority(self):
        self._make("C", 30)
        self._make("A", 10)
        self._make("B", 20)
        names = self.Provider.with_context(active_test=False).search([]).mapped("name")
        self.assertEqual(names, ["A", "B", "C"], "providers must order by priority")

    def test_token_present_false_without_env(self):
        p = self._make("NoTok", 10, env_key="ERA_LEADGEN_TEST_TOKEN_ABSENT")
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertFalse(p.token_present)

    def test_token_present_true_with_env(self):
        p = self._make("Tok", 10, env_key="ERA_LEADGEN_TEST_TOKEN_PRESENT")
        with mock.patch.dict("os.environ",
                             {"ERA_LEADGEN_TEST_TOKEN_PRESENT": "secret"}):
            self.assertTrue(p.token_present)

    def test_active_no_token_skipped_with_audit_not_silent(self):
        """Active + missing token => skipped AND a `blocked` audit row written.

        Exercises the single gate (the engine's _eligible_providers — the model's
        old _ready_providers duplicate was removed)."""
        from odoo.addons.era_crm_ai_agents_lead_gen.services.lead_gen_engine import (
            LeadGenEngine,
        )
        p = self._make("Active No Token", 10, env_key="ERA_LEADGEN_TEST_MISSING")
        Audit = self.env["crm.ai.audit.log"]
        before = Audit.search_count([("event_type", "=", "blocked")])
        engine = LeadGenEngine(self.env["crm.ai.lead_gen.agent"])
        with mock.patch.dict("os.environ", {}, clear=False):
            eligible = engine._eligible_providers(fetch_decision_makers=False)
        self.assertNotIn(p, eligible, "token-less active source must be skipped")
        after = Audit.search_count([("event_type", "=", "blocked")])
        self.assertEqual(after, before + 1,
                         "the skip must be audited (never silent)")

    def test_http_get_never_logs_the_token(self):
        """Rule 03: a network failure must NEVER put the API token into the audit
        log — not via the URL, the query string, or the exception text."""
        import requests
        agent = self.env["crm.ai.lead_gen.agent"]
        Audit = self.env["crm.ai.audit.log"]
        token = "SECRET-TOKEN-XYZ"

        def boom(*args, **kwargs):
            # urllib3/requests errors commonly echo the full URL incl. the token.
            raise requests.exceptions.ConnectionError(
                "HTTPSConnectionPool(host='serpapi.com', port=443): Max retries "
                "exceeded with url: /search.json?q=x&api_key=%s" % token)

        with mock.patch("requests.get", side_effect=boom):
            result = agent._http_get(
                "https://serpapi.com/search.json", params={"api_key": token})
        self.assertIsNone(result, "a failed fetch returns None (graceful skip)")
        row = Audit.search(
            [("event_type", "=", "source_fetch_failed")], order="id desc", limit=1)
        self.assertTrue(row, "the failure must be audited")
        blob = "%s %s" % (row.value_before or "", row.value_after or "")
        self.assertNotIn(token, blob, "token must never reach the audit log")
        self.assertIn("serpapi.com", blob, "host-only is safe and present")
