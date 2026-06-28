# -*- coding: utf-8 -*-
"""Task 2.8 — the explicitly required scenarios: the waterfall (a provider fails,
the next succeeds) and the per-billing-mode charge behavior."""
from odoo.tests.common import tagged

from .common import EnrichmentCase

_OK = {"status": "success", "billable": True}
_FAIL = {"data": {}, "channel": {}, "status": "no_data", "billable": True}


@tagged("post_install", "-at_install")
class TestWaterfall(EnrichmentCase):

    def test_fail_then_next_success(self):
        """Lower-priority provider fails; the chain CONTINUES and the next one
        fills the field, then stops."""
        self.register("wf_fail", "email", _FAIL)
        self.register("wf_ok", "email",
                      {"data": {"email": "ok@x.com"}, "channel": {}, **_OK})
        self.make_provider("wf_fail", "email", "email", priority=5,
                           billing_mode="per_success")
        self.make_provider("wf_ok", "email", "email", priority=6,
                           billing_mode="per_success")
        partner = self.make_partner(email=False)

        res = self.agent.enrich(partner, fields=["email"])

        self.assertEqual(partner.email, "ok@x.com")
        self.assertEqual(res["state"], "done")
        self.assertEqual(res["enriched"].get("email"), "wf_ok")
        # The failed provider WAS attempted (chain didn't stop on failure).
        self.assertEqual([t["provider"] for t in res["providers_tried"]],
                         ["wf_fail", "wf_ok"])

    def test_first_success_stops_chain(self):
        """The first success completes the need; no lower-priority provider for
        the same field is attempted."""
        self.register("wf_a", "email",
                      {"data": {"email": "a@x.com"}, "channel": {}, **_OK})
        self.register("wf_b", "email",
                      {"data": {"email": "b@x.com"}, "channel": {}, **_OK})
        self.make_provider("wf_a", "email", "email", priority=5)
        self.make_provider("wf_b", "email", "email", priority=6)
        partner = self.make_partner(email=False)

        res = self.agent.enrich(partner, fields=["email"])

        self.assertEqual(partner.email, "a@x.com")
        self.assertEqual([t["provider"] for t in res["providers_tried"]], ["wf_a"])

    def test_per_success_failure_not_charged(self):
        """A per_success provider that fails books NOTHING."""
        self.register("ps_fail", "email", _FAIL)
        self.make_provider("ps_fail", "email", "email", priority=5,
                           billing_mode="per_success", cost_per_call=0.02)
        partner = self.make_partner(email=False)

        before = self.n_source_rows()
        res = self.agent.enrich(partner, fields=["email"])

        self.assertEqual(self.n_source_rows() - before, 0)
        self.assertEqual(res["cost"], 0.0)
        self.assertEqual(res["state"], "failed")

    def test_per_success_success_is_charged(self):
        """A per_success provider that returns data books exactly its cost."""
        self.register("ps_ok", "email",
                      {"data": {"email": "ok@x.com"}, "channel": {}, **_OK})
        self.make_provider("ps_ok", "email", "email", priority=5,
                           billing_mode="per_success", cost_per_call=0.02)
        partner = self.make_partner(email=False)

        before = self.n_source_rows()
        res = self.agent.enrich(partner, fields=["email"])

        self.assertEqual(self.n_source_rows() - before, 1)
        self.assertAlmostEqual(res["cost"], 0.02)

    def test_per_call_failure_is_charged(self):
        """The binding model: a per_call provider that FAILS is still charged —
        the API call really cost money. (Distinct from the Notion shorthand
        'failed providers are not charged', which holds only for per_success.)"""
        self.register("pc_fail", "email", _FAIL)
        self.make_provider("pc_fail", "email", "email", priority=5,
                           billing_mode="per_call", cost_per_call=0.03)
        partner = self.make_partner(email=False)

        before = self.n_source_rows()
        res = self.agent.enrich(partner, fields=["email"])

        self.assertEqual(self.n_source_rows() - before, 1)
        self.assertAlmostEqual(res["cost"], 0.03)
        self.assertEqual(res["state"], "failed")

    def test_free_provider_never_charged(self):
        """A free provider books nothing even on a billable success."""
        self.register("free_p", "whatsapp",
                      {"data": {}, "channel": {"whatsapp": True},
                       "status": "success", "billable": True})
        self.make_provider("free_p", "whatsapp", "whatsapp", priority=5,
                           billing_mode="free", cost_per_call=0.0)
        partner = self.make_partner()

        before = self.n_source_rows()
        res = self.agent.enrich(partner, fields=["whatsapp"])

        self.assertEqual(self.n_source_rows() - before, 0)
        self.assertEqual(res["cost"], 0.0)
        self.assertEqual(res["contactability"].get("whatsapp"), True)
