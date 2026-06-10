# -*- coding: utf-8 -*-
"""Compliance integration tests: the three scenarios the task names
(send-without-consent → blocked, prayer-time → deferred, opt-out flow), plus
DSAR, the signed opt-out token, and proof that guard() is callable from a
salesperson-scoped (non-superuser) agent — Rule 09.

``now`` is pinned with mock.patch on fields.Datetime.now so timing is
deterministic regardless of when the suite runs.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.era_crm_ai_agents_compliance.services.guard import (
    ComplianceGuard, guard as guard_fn,
)
from odoo.addons.era_crm_ai_agents_compliance.services import opt_out

_TZ = "Asia/Riyadh"
_GOOD_TEXT = "السلام عليكم أستاذ خالد، تحية طيبة."
_BAD_TEXT = "ارسل الفلوس"  # no greeting, no honorific


def _riyadh_utc(y, mo, d, h, mi):
    local = pytz.timezone(_TZ).localize(datetime(y, mo, d, h, mi))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


_GOOD_TIME = _riyadh_utc(2026, 6, 10, 10, 0)   # 10:00 Riyadh — allowed
_PRAYER_TIME = _riyadh_utc(2026, 6, 10, 12, 5)  # 12:05 Riyadh — Dhuhr


@tagged("post_install", "-at_install")
class TestComplianceGuard(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({
            "name": "Khalid Test", "tz": _TZ,
        })
        self.Consent = self.env["crm.ai.consent"]
        self.Audit = self.env["crm.ai.audit.log"]

    # -- helpers --------------------------------------------------------
    def _grant(self):
        self.Consent.register_consent(
            self.partner, consent_type="marketing", state="granted", source="test")

    def _audit_count(self):
        return self.Audit.sudo().search_count(
            [("model_ref", "=", "res.partner,%d" % self.partner.id)])

    # -- scenario 1: send without consent -------------------------------
    def test_send_without_consent_blocked(self):
        before = self._audit_count()
        with patch.object(fields.Datetime, "now", return_value=_GOOD_TIME):
            d = ComplianceGuard(self.env).guard(self.partner, _GOOD_TEXT, "whatsapp")
        self.assertFalse(d["allowed"])
        self.assertIn("consent", d["reason"])
        self.assertGreater(self._audit_count(), before)

    # -- scenario 2: prayer-time send is deferred -----------------------
    def test_prayer_time_deferred(self):
        self._grant()
        with patch.object(fields.Datetime, "now", return_value=_PRAYER_TIME):
            d = ComplianceGuard(self.env).guard(self.partner, _GOOD_TEXT, "whatsapp")
        self.assertFalse(d["allowed"])
        self.assertIn("prayer", d["reason"])
        self.assertTrue(d["deferred_until"], "a concrete next slot must be returned")

    # -- allow path -----------------------------------------------------
    def test_allowed_with_consent_good_time_good_text(self):
        self._grant()
        with patch.object(fields.Datetime, "now", return_value=_GOOD_TIME):
            d = ComplianceGuard(self.env).guard(self.partner, _GOOD_TEXT, "whatsapp")
        self.assertTrue(d["allowed"])
        self.assertIsNone(d["deferred_until"])

    # -- norms block ----------------------------------------------------
    def test_norms_violation_blocks(self):
        self._grant()
        with patch.object(fields.Datetime, "now", return_value=_GOOD_TIME):
            d = ComplianceGuard(self.env).guard(self.partner, _BAD_TEXT, "whatsapp")
        self.assertFalse(d["allowed"])
        self.assertIn("cultural norms", d["reason"])

    # -- every call audits ----------------------------------------------
    def test_every_guard_call_writes_one_audit(self):
        before = self._audit_count()
        with patch.object(fields.Datetime, "now", return_value=_GOOD_TIME):
            ComplianceGuard(self.env).guard(self.partner, _GOOD_TEXT, "whatsapp")
        self.assertEqual(self._audit_count(), before + 1)

    # -- scenario 3: opt-out flow ---------------------------------------
    def test_opt_out_flow(self):
        self._grant()
        self.assertTrue(self.Consent.has_consent(self.partner))
        opt_out.process_opt_out(self.env, self.partner, source="test")
        self.assertFalse(self.Consent.has_consent(self.partner))
        self.assertFalse(self.partner.crm_ai_intl_processing_consent)
        self.assertTrue(self.partner.crm_ai_opt_out_requested_on)

    def test_cron_enforces_pending_opt_out(self):
        # A request recorded but still showing consent, older than 72h.
        self.partner.write({
            "crm_ai_intl_processing_consent": True,
            "crm_ai_opt_out_requested_on": fields.Datetime.now() - timedelta(hours=100),
        })
        self.env["res.partner"].cron_enforce_72h()
        self.assertFalse(self.partner.crm_ai_intl_processing_consent)

    # -- DSAR -----------------------------------------------------------
    def test_dsar_access_returns_history(self):
        self._grant()
        data = self.Consent.handle_dsar(self.partner, "access")
        self.assertIsInstance(data, list)
        self.assertTrue(any(r["state"] == "granted" for r in data))

    def test_dsar_erasure_anonymizes(self):
        self._grant()
        count = self.Consent.handle_dsar(self.partner, "erasure")
        self.assertGreater(count, 0)
        # The partner link is severed; rows survive only as anonymized residue.
        self.assertFalse(self.Consent.sudo().search([("partner_id", "=", self.partner.id)]))
        self.assertTrue(self.Consent.sudo().search([("erased", "=", True)]))

    # -- opt-out token --------------------------------------------------
    def test_opt_out_token_roundtrip(self):
        token = opt_out.make_token(self.env, self.partner)
        self.assertTrue(opt_out.verify_token(self.env, self.partner.id, token))
        self.assertFalse(opt_out.verify_token(self.env, self.partner.id, token[:-1] + "x"))
        self.assertFalse(opt_out.verify_token(self.env, self.partner.id + 1, token))

    # -- guard() callable from a sample (salesperson-scoped) agent ------
    def test_guard_callable_from_sample_agent(self):
        """A sending agent runs under a salesperson, never superuser (Rule 09).
        Prove guard() is callable in exactly that context and returns a decision."""
        rep = self.env["res.users"].create({
            "name": "Sales Rep",
            "login": "rep_compliance_guard_test",
            "group_ids": [(4, self.env.ref(
                "era_crm_ai_agents_base.group_crm_ai_user").id)],
        })
        self._grant()
        with patch.object(fields.Datetime, "now", return_value=_GOOD_TIME):
            decision = guard_fn(
                self.env(user=rep), self.partner, _GOOD_TEXT, "whatsapp")
        self.assertIn("allowed", decision)
        self.assertTrue(decision["allowed"])
