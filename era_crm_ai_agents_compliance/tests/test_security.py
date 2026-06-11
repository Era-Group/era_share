# -*- coding: utf-8 -*-
"""Security / ACL tests (task 1.2, incl. the reopen reason: ACL for the new
manager-configurable models).

Proves the permission boundaries actually hold for a salesperson-scoped user:
  - consent log is append-only (create + read, no write/unlink);
  - norm vocabulary is read-only for users (managers edit it);
  - the prayer cache is user-writable reference data (so a send-time cache miss
    can be filled without sudo), but not deletable by users.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSecurity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = self.env["res.users"].create({
            "name": "Sales Rep Sec",
            "login": "rep_compliance_sec_test",
            "group_ids": [(4, self.env.ref(
                "era_crm_ai_agents_base.group_crm_ai_user").id)],
        })
        self.manager = self.env["res.users"].create({
            "name": "Compliance Mgr",
            "login": "mgr_compliance_sec_test",
            "group_ids": [(4, self.env.ref(
                "era_crm_ai_agents_base.group_crm_ai_manager").id)],
        })
        self.partner = self.env["res.partner"].create({"name": "Sec Partner"})

    # -- consent: append-only for users --------------------------------
    def test_consent_append_only_for_user(self):
        Consent = self.env["crm.ai.consent"].with_user(self.user)
        row = Consent.create({
            "partner_id": self.partner.id,
            "consent_type": "marketing", "state": "granted"})  # create OK
        self.assertTrue(row.id)
        with self.assertRaises(AccessError):
            row.write({"state": "withdrawn"})                  # no write
        with self.assertRaises(AccessError):
            row.unlink()                                       # no unlink

    def test_consent_manager_can_write(self):
        row = self.env["crm.ai.consent"].create({
            "partner_id": self.partner.id,
            "consent_type": "marketing", "state": "granted"})
        # manager has write (needed for DSAR anonymize) — should not raise
        row.with_user(self.manager).write({"source": "mgr-edit"})
        self.assertEqual(row.source, "mgr-edit")

    # -- norm vocabulary: read-only for users --------------------------
    def test_norm_term_readonly_for_user(self):
        Term = self.env["crm.ai.norm.term"]
        Term.with_user(self.user).search([], limit=1)          # read OK
        with self.assertRaises(AccessError):
            Term.with_user(self.user).create(
                {"category": "greeting", "text": "x"})         # no create
        # manager can create
        Term.with_user(self.manager).create(
            {"category": "greeting", "text": "mgr greeting"})

    # -- prayer cache: user-writable reference data --------------------
    def test_prayer_cache_user_writable_not_deletable(self):
        Cache = self.env["crm.ai.prayer.cache"].with_user(self.user)
        row = Cache.create({"city": "TestCity", "country": "SA",
                            "date": "2026-06-15"})             # create OK
        row.write({"dhuhr": "12:00"})                          # write OK
        self.assertEqual(row.dhuhr, "12:00")
        with self.assertRaises(AccessError):
            row.unlink()                                       # no unlink
