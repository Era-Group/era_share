"""Shared helpers for the Email Verification tests.

The client model is the only network seam, so tests patch its methods
(``create_job`` / ``get_job`` / ``get_results`` / ``delete_job`` /
``verify_one``) and never touch the network.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase


def make_result(email, status="deliverable", score=95, flags=None,
                reason_codes=None, smtp_code=250, smtp_message="250 OK"):
    """Build a raw verifier result dict shaped like the API response.

    Handles an address with no ``@`` too: the verifier answers those with
    ``undeliverable`` / ``invalid_syntax`` and null local/domain parts, so
    tests must be able to build that shape.
    """
    base_flags = {
        "syntax_valid": True, "disposable": False, "role": False,
        "suppressed": False, "catch_all": False, "free_provider": False,
    }
    base_flags.update(flags or {})
    local, _, domain = email.partition("@")
    return {
        "email": email,
        "normalized_email": email if domain else None,
        "local_part": local if domain else None,
        "domain": domain or None,
        "status": status,
        "score": score,
        "reason_codes": reason_codes or [status],
        "flags": base_flags,
        "mx_records": ["mx.%s" % domain] if domain else [],
        "smtp_code": smtp_code,
        "smtp_message": smtp_message,
        "smtp_host": "mx.%s" % domain if domain else None,
        "checked_at": "2026-08-01T00:00:00+00:00",
        "elapsed_ms": 12,
    }


class EVCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("era_email_verification.base_url", "https://verifier.test")
        icp.set_param("era_email_verification.api_key", "k" * 32)
        # Auto-blacklist checkboxes: the shipped default (Undeliverable + Risky).
        icp.set_param("era_email_verification.blacklist_undeliverable", "True")
        icp.set_param("era_email_verification.blacklist_risky", "True")
        icp.set_param("era_email_verification.blacklist_disposable", "False")
        icp.set_param("era_email_verification.blacklist_unknown", "False")
        icp.set_param("era_email_verification.blacklist_catch_all", "False")
        icp.set_param("era_email_verification.min_eligible_score", "80")
        cls.Batch = cls.env["email.verification.batch"]
        cls.Item = cls.env["email.verification.item"]
        cls.client_cls = type(cls.env["email.verification.client"])

    def patch_client(self, method, **kwargs):
        """Convenience: patch.object on the client registry class."""
        return patch.object(self.client_cls, method, **kwargs)

    def patch_cron_progress(self):
        """Neutralize ``ir.cron._commit_progress`` for a direct cron call.

        The real one commits the cursor, which a TransactionCase forbids.
        Returning ``inf`` also means "plenty of time left", so the cron under
        test runs all of its steps.
        """
        return patch.object(
            type(self.env["ir.cron"]), "_commit_progress",
            lambda *args, **kwargs: float("inf"))

    def blacklisted(self, email):
        return self.env["mail.blacklist"].with_context(active_test=False).search(
            [("email", "=", email)])
