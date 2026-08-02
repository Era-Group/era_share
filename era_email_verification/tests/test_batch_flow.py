"""End-to-end batch flow: enqueue -> submit -> import -> apply + blacklist."""
from odoo.tests import tagged

from .common import EVCommon, make_result


@tagged("post_install", "-at_install")
class TestBatchFlow(EVCommon):

    def test_full_flow_updates_partner_and_blacklists(self):
        partner = self.env["res.partner"].create(
            {"name": "Alice", "email": "alice@example.com"})
        contact = self.env["mailing.contact"].create(
            {"name": "Bob", "email": "bob@example.com"})

        targets = self.Batch._prepare_targets(partners=partner, mailing_contacts=contact)
        batches = self.Batch._enqueue_targets(
            targets, check_smtp=True, check_catch_all=True, source="partners")
        self.assertEqual(len(batches), 1)
        batch = batches
        self.assertEqual(batch.state, "queued")
        self.assertEqual(len(batch.item_ids), 2)

        # -- submit --
        with self.patch_client("create_job",
                               return_value={"job_id": "job-1", "status": "queued", "total": 2}):
            batch._submit()
        self.assertEqual(batch.state, "running")
        self.assertEqual(batch.job_id, "job-1")

        # -- import: alice deliverable, bob undeliverable --
        page = {"items": [
            {"email": "alice@example.com", "state": "done",
             "result": make_result("alice@example.com", status="deliverable", score=95)},
            {"email": "bob@example.com", "state": "done",
             "result": make_result("bob@example.com", status="undeliverable", score=0,
                                    reason_codes=["mailbox_not_found"])},
        ]}
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True) as delete_mock:
            imported = batch._import_available(page_limit=500)

        self.assertEqual(imported, 2)
        self.assertEqual(batch.state, "done")
        delete_mock.assert_called_once()

        partner.invalidate_recordset()
        contact.invalidate_recordset()
        self.assertEqual(partner.email_verification_status, "deliverable")
        self.assertTrue(partner.email_verification_eligible)
        self.assertEqual(partner.email_verification_checked_email, "alice@example.com")
        self.assertEqual(contact.email_verification_status, "undeliverable")

        # undeliverable bob -> blacklisted (policy undeliverable_risky); alice not
        self.assertTrue(self.blacklisted("bob@example.com"))
        self.assertFalse(self.blacklisted("alice@example.com"))
        self.assertEqual(batch.blacklist_added_count, 1)
        self.assertEqual(batch.deliverable_count, 1)
        self.assertEqual(batch.undeliverable_count, 1)

    def test_catch_all_is_not_blacklisted_by_default(self):
        # Catch-all is "unverifiable", not invalid, so the default policy keeps
        # it as a separate risky segment and does NOT blacklist it.
        partner = self.env["res.partner"].create(
            {"name": "Cara", "email": "cara@catchall.test"})
        targets = self.Batch._prepare_targets(partners=partner)
        batch = self.Batch._enqueue_targets(targets, source="partners")
        with self.patch_client("create_job",
                               return_value={"job_id": "j2", "status": "queued", "total": 1}):
            batch._submit()
        page = {"items": [{"email": "cara@catchall.test", "state": "done",
                           "result": make_result("cara@catchall.test", status="risky",
                                                  score=55, flags={"catch_all": True},
                                                  reason_codes=["catch_all_domain"])}]}
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True):
            batch._import_available()
        self.assertFalse(self.blacklisted("cara@catchall.test"))
        self.assertEqual(batch.risky_count, 1)
        self.assertEqual(batch.blacklist_added_count, 0)

    def test_catch_all_is_blacklisted_when_opted_in(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.blacklist_catch_all", "True")
        partner = self.env["res.partner"].create(
            {"name": "Cid", "email": "cid@catchall2.test"})
        batch = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=partner), source="partners")
        with self.patch_client("create_job",
                               return_value={"job_id": "j3", "status": "queued", "total": 1}):
            batch._submit()
        page = {"items": [{"email": "cid@catchall2.test", "state": "done",
                           "result": make_result("cid@catchall2.test", status="risky",
                                                  score=55, flags={"catch_all": True})}]}
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True):
            batch._import_available()
        self.assertTrue(self.blacklisted("cid@catchall2.test"))

    def test_single_verify_button(self):
        partner = self.env["res.partner"].create(
            {"name": "Dan", "email": "dan@example.com"})
        with self.patch_client(
                "verify_one",
                return_value=make_result("dan@example.com", status="deliverable", score=90)):
            action = partner.action_verify_email()
        self.assertEqual(partner.email_verification_status, "deliverable")
        self.assertEqual(partner.email_verification_history_count, 1)
        # The button returns a client action, which leaves the form showing
        # the values it loaded before the check — the status badge would still
        # read "Not checked" until a manual refresh. The notification must
        # chain a reload so the new status shows immediately.
        self.assertEqual(action["params"]["next"],
                         {"type": "ir.actions.client", "tag": "soft_reload"})
