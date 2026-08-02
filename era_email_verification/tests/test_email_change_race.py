"""Email-change race: a result must never overwrite a now-different email."""
from odoo.tests import tagged

from .common import EVCommon, make_result


@tagged("post_install", "-at_install")
class TestEmailChangeRace(EVCommon):

    def test_changed_email_marks_item_stale_and_skips_write(self):
        partner = self.env["res.partner"].create(
            {"name": "Eve", "email": "eve@old.com"})
        targets = self.Batch._prepare_targets(partners=partner)
        batch = self.Batch._enqueue_targets(targets, source="partners")
        with self.patch_client("create_job",
                               return_value={"job_id": "j", "status": "queued", "total": 1}):
            batch._submit()

        # The contact's email changes *after* submission but before import.
        partner.write({"email": "eve@new.com"})

        page = {"items": [{"email": "eve@old.com", "state": "done",
                           "result": make_result("eve@old.com", status="deliverable",
                                                  score=95)}]}
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True):
            batch._import_available()

        partner.invalidate_recordset()
        item = batch.item_ids
        # The stale result must NOT have been written onto the new email.
        self.assertNotEqual(partner.email_verification_status, "deliverable")
        self.assertEqual(item.state, "stale")
        self.assertEqual(batch.stale_count, 1)

    def test_email_change_resets_verified_status(self):
        partner = self.env["res.partner"].create(
            {"name": "Fay", "email": "fay@example.com"})
        # simulate a prior verification
        partner.with_context(ev_apply_result=True).write({
            "email_verification_status": "deliverable",
            "email_verification_score": 95,
            "email_verification_checked_email": "fay@example.com",
            "email_verification_eligible": True,
        })
        # changing the email must reset status and flag re-check
        partner.write({"email": "fay@changed.com"})
        self.assertEqual(partner.email_verification_status, "not_checked")
        self.assertFalse(partner.email_verification_eligible)
        self.assertTrue(partner.email_verification_needs_recheck)
        self.assertFalse(partner.email_verification_checked_email)
