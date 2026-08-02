"""Pure-logic tests: result normalization, eligibility and blacklist policy."""
from odoo.tests import TransactionCase

from odoo.addons.era_email_verification.models import constants


class TestConstants(TransactionCase):

    def test_normalize_result_maps_fields(self):
        result = {
            "status": "risky", "score": 55,
            "reason_codes": ["catch_all_domain", "smtp_recipient_accepted"],
            "flags": {"catch_all": True, "role": True, "disposable": False,
                      "free_provider": False},
            "mx_records": ["mx1.x", "mx2.x"],
            "smtp_code": 250, "smtp_message": "250 OK",
            "checked_at": "2026-08-01T12:00:00+00:00", "elapsed_ms": 20,
        }
        vals = self.env["email.verification.item"]._normalize_result(result)
        self.assertEqual(vals["status"], "risky")
        self.assertEqual(vals["score"], 55)
        self.assertEqual(vals["reason_summary"], "catch_all_domain")
        self.assertEqual(vals["reason_codes"], "catch_all_domain, smtp_recipient_accepted")
        self.assertTrue(vals["catch_all"])
        self.assertTrue(vals["role_account"])
        self.assertEqual(vals["mx_records"], "mx1.x, mx2.x")
        self.assertEqual(vals["smtp_code"], "250")
        # ISO with offset -> naive UTC datetime
        self.assertEqual(str(vals["checked_at"]), "2026-08-01 12:00:00")
        self.assertTrue(vals["_flags"]["catch_all"])

    def test_smtp_code_none_is_falsy(self):
        vals = self.env["email.verification.item"]._normalize_result(
            {"status": "unknown", "smtp_code": None})
        self.assertFalse(vals["smtp_code"])

    def test_is_eligible_policy(self):
        # deliverable + high score + not disposable -> eligible
        self.assertTrue(constants.is_eligible("deliverable", 95, {}, 80))
        # below threshold -> not eligible
        self.assertFalse(constants.is_eligible("deliverable", 70, {}, 80))
        # disposable deliverable -> never eligible
        self.assertFalse(constants.is_eligible("deliverable", 95, {"disposable": True}, 80))
        # risky / unknown / undeliverable -> never eligible
        for status in ("risky", "unknown", "undeliverable"):
            self.assertFalse(constants.is_eligible(status, 95, {}, 80))

    @staticmethod
    def _policy(*enabled):
        """Build a checkbox policy with only ``enabled`` outcomes ticked."""
        return {outcome: outcome in enabled
                for outcome in constants.BLACKLIST_OUTCOMES}

    def test_should_blacklist_matrix(self):
        risky_flags = {"disposable": False}
        disp_flags = {"disposable": True}
        # nothing ticked -> never (the old "Off" policy)
        nothing = self._policy()
        for status in ("undeliverable", "risky", "unknown", "deliverable"):
            self.assertFalse(constants.should_blacklist(nothing, status, {}))
        # each box only covers its own outcome
        self.assertTrue(constants.should_blacklist(
            self._policy("undeliverable"), "undeliverable", {}))
        self.assertFalse(constants.should_blacklist(
            self._policy("undeliverable"), "risky", risky_flags))
        self.assertFalse(constants.should_blacklist(
            self._policy("undeliverable"), "unknown", {}))
        # disposable box: catches disposable-flagged risky, not plain risky
        disposable_only = self._policy("disposable")
        self.assertTrue(constants.should_blacklist(disposable_only, "risky", disp_flags))
        self.assertFalse(constants.should_blacklist(disposable_only, "risky", risky_flags))
        # risky box covers disposable too (it is a risky result)
        self.assertTrue(constants.should_blacklist(
            self._policy("risky"), "risky", disp_flags))
        # unknown box
        self.assertTrue(constants.should_blacklist(self._policy("unknown"), "unknown", {}))
        self.assertFalse(constants.should_blacklist(self._policy("risky"), "unknown", {}))
        # deliverable is never blacklisted, whatever is ticked
        every = self._policy(*constants.BLACKLIST_OUTCOMES)
        self.assertFalse(constants.should_blacklist(every, "deliverable", {}))

    def test_defaults_match_the_recommended_policy(self):
        # Shipped defaults = the old "Undeliverable + Risky (recommended)".
        default = constants.DEFAULT_BLACKLIST_OUTCOMES
        self.assertTrue(constants.should_blacklist(default, "undeliverable", {}))
        self.assertTrue(constants.should_blacklist(default, "risky", {"role": True}))
        self.assertFalse(constants.should_blacklist(default, "unknown", {}))
        self.assertFalse(constants.should_blacklist(default, "risky", {"catch_all": True}))

    def test_catch_all_has_its_own_box(self):
        catchall = {"catch_all": True}
        # Ticking Risky does NOT blacklist catch-all: it answers to its own box.
        self.assertFalse(constants.should_blacklist(
            self._policy("undeliverable", "risky"), "risky", catchall))
        self.assertTrue(constants.should_blacklist(
            self._policy("catch_all"), "risky", catchall))
        # A non-catch-all risky (e.g. role account) is not caught by that box.
        self.assertFalse(constants.should_blacklist(
            self._policy("catch_all"), "risky", {"role": True}))
        # An undeliverable catch-all follows the Undeliverable box.
        self.assertTrue(constants.should_blacklist(
            self._policy("undeliverable"), "undeliverable", catchall))
