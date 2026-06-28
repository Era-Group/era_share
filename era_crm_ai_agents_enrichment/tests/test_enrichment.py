# -*- coding: utf-8 -*-
"""Task 2.8 — broader install/behaviour coverage of the 2.0–2.7 guarantees:
governance toggle, cost cap, deliverability persistence, fail-closed consent,
the per-provider analytics substrate, the scheduling cron, and provider ACL."""
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from odoo.addons.era_crm_ai_agents_enrichment.services import deliverability as D
from .common import EnrichmentCase

_OK_EMAIL = {"data": {"email": "ok@x.com"},
             "channel": {"email": True}, "status": "success", "billable": True}
_NS = "era_crm_ai_agents_enrichment."


@tagged("post_install", "-at_install")
class TestEnrichmentModule(EnrichmentCase):

    # -- install sanity -------------------------------------------------------
    def test_install_seeds(self):
        """The module installed: agent row + the 7 default providers exist."""
        self.assertTrue(self.agent_rec, "Enrichment agent row must be seeded.")
        self.assertGreaterEqual(
            self.Provider.with_context(active_test=False).search_count([]), 7)

    # -- governance -----------------------------------------------------------
    def test_master_toggle_off_is_inert(self):
        self.register("any", "email", _OK_EMAIL)
        self.make_provider("any", "email", "email")
        self.ICP.set_param(_NS + "enabled", "False")
        partner = self.make_partner(email=False)

        res = self.agent.enrich(partner, fields=["email"])

        self.assertFalse(res["enabled"])
        self.assertFalse(res["request_id"])
        self.assertFalse(partner.email)

    # -- cost cap -------------------------------------------------------------
    def test_cap_stops_run_with_audit(self):
        self.register("capf", "email", _OK_EMAIL)
        self.make_provider("capf", "email", "email", cost_per_call=0.5)
        # Blow past a tiny per-agent cap before the run starts.
        self.ICP.set_param("era_crm_ai_agents.monthly_cost_cap.era_enrichment", "0.01")
        self.Usage.record(self.agent_rec, None, 0, 0, 9.0, usage_type="source_api")
        partner = self.make_partner(email=False)

        before_audit = self.Audit.search_count(
            [("event_type", "=", "cost_cap_exceeded")])
        res = self.agent.enrich(partner, fields=["email"])

        self.assertEqual(res["state"], "capped")
        self.assertEqual(res["providers_tried"], [])
        self.assertFalse(partner.email)
        self.assertEqual(
            self.Audit.search_count([("event_type", "=", "cost_cap_exceeded")]),
            before_audit + 1)

    # -- deliverability persistence ------------------------------------------
    def test_deliverability_flags_persisted(self):
        self.register("delv", "email",
                      {"data": {}, "channel": {"email": True},
                       "status": "success", "billable": True})
        self.make_provider("delv", "email", "email")
        partner = self.make_partner(email="a@x.com")

        self.agent.enrich(partner, request_type="contactability")

        self.assertTrue(partner.email_valid)
        self.assertTrue(partner.crm_ai_deliverability_checked)

    def test_unknown_verdict_does_not_write_flag(self):
        self.register("unk", "email",
                      {"data": {}, "channel": {"email": None},
                       "status": "no_data", "billable": False})
        self.make_provider("unk", "email", "email")
        partner = self.make_partner(email="a@x.com")
        partner.email_valid = False

        self.agent.enrich(partner, request_type="contactability")

        # None verdict leaves the flag untouched (no false "checked").
        self.assertFalse(partner.email_valid)
        self.assertFalse(partner.crm_ai_deliverability_checked)

    # -- consent (fail-closed) ------------------------------------------------
    def test_consent_ok_fail_closed(self):
        p_no = self.make_partner(consent=False)
        p_yes = self.make_partner(consent=True)
        self.assertFalse(D.consent_ok(self.env, p_no))
        self.assertTrue(D.consent_ok(self.env, p_yes))
        self.assertFalse(D.consent_ok(self.env, self.env["res.partner"]))

    def test_verify_email_no_consent_no_call(self):
        p_no = self.make_partner(consent=False)
        # Without consent the verifier returns 'unknown' and never reads a token
        # or issues a call (billable False).
        out = D.verify_email(self.env, p_no, "x@y.com", "ERA_ENRICH_ZEROBOUNCE")
        self.assertEqual(out, {"valid": None, "billable": False, "status": "unknown"})

    # -- analytics substrate (2.7) -------------------------------------------
    def test_attempts_and_provider_stats(self):
        self.register("ok", "email", _OK_EMAIL)
        self.register("bad", "phone",
                      {"data": {}, "channel": {"phone": None},
                       "status": "no_data", "billable": True})
        ok = self.make_provider("ok", "email", "email", priority=5)
        bad = self.make_provider("bad", "phone", "phone", priority=6)
        for i in range(2):
            self.agent.enrich(self.make_partner(email=False, phone="123%d" % i),
                              request_type="contactability")

        self.assertEqual(
            self.env["crm.ai.enrichment.attempt"].search_count([]), 4)
        ok.invalidate_recordset()
        bad.invalidate_recordset()
        self.assertEqual(ok.attempt_count, 2)
        self.assertAlmostEqual(ok.success_rate, 100.0)
        self.assertAlmostEqual(bad.success_rate, 0.0)

    # -- scheduling cron (2.6) -----------------------------------------------
    def test_cron_enrich_stale_batch(self):
        self.register("cron_e", "email",
                      {"data": {}, "channel": {"email": True},
                       "status": "success", "billable": True})
        self.make_provider("cron_e", "email", "email")
        self.ICP.set_param(_NS + "cron_batch_size", "2")
        # 3 never-checked stale partners with a contact method.
        partners = [self.make_partner(email="s%d@x.com" % i) for i in range(3)]

        processed = self.agent.cron_enrich_stale()

        self.assertEqual(processed, 2)  # bounded by batch size
        verified = sum(1 for p in partners if p.email_valid)
        self.assertEqual(verified, 2)

    def test_cron_inert_when_disabled(self):
        self.ICP.set_param(_NS + "enabled", "False")
        self.make_partner(email="z@x.com")
        self.assertEqual(self.agent.cron_enrich_stale(), 0)

    # -- security (2.2) -------------------------------------------------------
    def test_provider_config_manager_only(self):
        user = self.env["res.users"].create({
            "name": "Enrich User", "login": "enrich_user_28",
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("era_crm_ai_agents_base.group_crm_ai_user").id])]})
        vals = {"name": "X", "provider_type": "email", "fields_filled": "email"}
        with self.assertRaises(AccessError):
            self.Provider.with_user(user).create(vals)

        manager = self.env["res.users"].create({
            "name": "Enrich Mgr", "login": "enrich_mgr_28",
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("era_crm_ai_agents_base.group_crm_ai_manager").id])]})
        # Manager can create — no raise.
        self.Provider.with_user(manager).create(vals)
