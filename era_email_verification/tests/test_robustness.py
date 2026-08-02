"""Regression tests for the failure modes that used to strand a batch:

* an address ``email_normalize`` cannot parse crashing the whole import;
* the auto re-check cron enqueueing the same contacts every single tick;
* a running batch that stops progressing blocking the one-at-a-time queue;
* cancel / fail / reset leaving the remote job behind (or stranding the batch).
"""
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import EVCommon, make_result


@tagged("post_install", "-at_install")
class TestInvalidAddressHandling(EVCommon):

    def test_unparseable_address_does_not_sink_the_page(self):
        """mail.blacklist.create() raises UserError on an address
        email_normalize() can't parse — and an invalid address is exactly what
        comes back 'undeliverable'. It must be skipped, not fail the import."""
        bad = self.env["res.partner"].create(
            {"name": "Bad", "email": "not-an-email"})
        good = self.env["res.partner"].create(
            {"name": "Good", "email": "good@example.com"})
        batch = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=bad | good), source="partners")
        with self.patch_client("create_job",
                               return_value={"job_id": "j-bad", "total": 2}):
            batch._submit()

        page = {"items": [
            {"email": "not-an-email", "state": "done",
             "result": make_result("not-an-email", status="undeliverable",
                                   score=0, reason_codes=["invalid_syntax"])},
            {"email": "good@example.com", "state": "done",
             "result": make_result("good@example.com", status="undeliverable",
                                   score=0, reason_codes=["mailbox_not_found"])},
        ]}
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True):
            imported = batch._import_available()

        # Both imported, batch completed, and the good address still blacklisted.
        self.assertEqual(imported, 2)
        self.assertEqual(batch.state, "done")
        self.assertTrue(self.blacklisted("good@example.com"))
        bad.invalidate_recordset()
        self.assertEqual(bad.email_verification_status, "undeliverable")
        # The unparseable one is recorded but never sent to mail.blacklist.
        bad_item = batch.item_ids.filtered(lambda i: i.email == "not-an-email")
        self.assertEqual(bad_item.state, "done")
        self.assertFalse(bad_item.blacklisted)

    def test_junk_email_values_never_reach_the_payload(self):
        """The verifier rejects the WHOLE job with 422 if a single entry is
        outside 3..320 chars. Junk sitting in an email field would therefore
        fail every batch it lands in — permanently, because the re-check sweep
        keeps reselecting the same addresses."""
        junk = self.env["res.partner"].create([
            {"name": "J%d" % i, "email": e}
            for i, e in enumerate((".", "00", "Mm", "……", " ", "a" * 400))])
        good = self.env["res.partner"].create(
            {"name": "Good", "email": "real@example.com"})

        targets = self.Batch._prepare_targets(partners=junk | good)
        emails = [t["email"] for t in targets]
        self.assertEqual(emails, ["real@example.com"])

        # ...and every address that does get through satisfies the contract.
        for t in targets:
            self.assertTrue(self.Batch._is_submittable(t["email"]))

    def test_plausible_but_invalid_addresses_are_still_submitted(self):
        """Only what the API refuses is dropped. A syntactically broken but
        normal-length address must still be sent — 'undeliverable' back from
        the verifier is useful data-quality signal."""
        partner = self.env["res.partner"].create(
            {"name": "Odd", "email": "not-an-email"})
        targets = self.Batch._prepare_targets(partners=partner)
        self.assertEqual([t["email"] for t in targets], ["not-an-email"])

    def test_apply_error_is_isolated_to_its_item(self):
        """Any other per-item failure marks that item error and keeps going."""
        p1 = self.env["res.partner"].create({"name": "A", "email": "a1@x.com"})
        p2 = self.env["res.partner"].create({"name": "B", "email": "b1@x.com"})
        batch = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=p1 | p2), source="partners")
        with self.patch_client("create_job",
                               return_value={"job_id": "j-err", "total": 2}):
            batch._submit()

        real_write_targets = type(batch)._write_targets

        def boom(self_batch, item, vals, flags, policy, min_score):
            if item.email == "a1@x.com":
                raise ValueError("synthetic failure")
            return real_write_targets(self_batch, item, vals, flags, policy, min_score)

        page = {"items": [
            {"email": "a1@x.com", "state": "done", "result": make_result("a1@x.com")},
            {"email": "b1@x.com", "state": "done", "result": make_result("b1@x.com")},
        ]}
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True), \
             patch.object(type(batch), "_write_targets", boom):
            batch._import_available()

        items = {i.email: i for i in batch.item_ids}
        self.assertEqual(items["a1@x.com"].state, "error")
        self.assertFalse(items["a1@x.com"].status)
        self.assertEqual(items["a1@x.com"].score, 0)
        self.assertIn("synthetic failure", items["a1@x.com"].error_message)
        self.assertEqual(items["b1@x.com"].state, "done")
        self.assertEqual(batch.import_offset, 0)
        self.assertEqual(batch.state, "running")
        p2.invalidate_recordset()
        self.assertEqual(p2.email_verification_status, "deliverable")

        # The next reconcile retries the failed prefix item, skips the already
        # imported second item, and can then advance/finalize safely.
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True):
            imported = batch._import_available()
        self.assertEqual(imported, 1)
        self.assertEqual(batch.import_offset, 2)
        self.assertEqual(batch.state, "done")


@tagged("post_install", "-at_install")
class TestAutoRecheckGuard(EVCommon):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.auto_recheck", "True")

    def test_recheck_sweep_does_not_pile_up_duplicate_batches(self):
        self.env["res.partner"].create(
            [{"name": "R%d" % i, "email": "r%d@x.com" % i} for i in range(3)])
        first = self.Batch._cron_enqueue_unchecked(limit=100)
        self.assertTrue(first)
        self.assertEqual(first.state, "queued")

        # The contacts still read as unchecked until results land, so a second
        # tick would previously enqueue the very same addresses all over again.
        second = self.Batch._cron_enqueue_unchecked(limit=100)
        self.assertFalse(second)
        self.assertEqual(
            self.Batch.search_count([("source", "=", "recheck")]), 1)

    def test_sweep_resumes_once_the_previous_recheck_batch_is_done(self):
        self.env["res.partner"].create({"name": "R", "email": "r@x.com"})
        first = self.Batch._cron_enqueue_unchecked(limit=100)
        first.write({"state": "done"})
        self.env["res.partner"].create({"name": "S", "email": "s@x.com"})
        self.assertTrue(self.Batch._cron_enqueue_unchecked(limit=100))

    def test_sweep_backs_off_after_a_failed_recheck_batch(self):
        """A verifier that is down must not make the sweep manufacture an
        identical failed batch on every single run."""
        self.env["res.partner"].create({"name": "R", "email": "r@x.com"})
        first = self.Batch._cron_enqueue_unchecked(limit=100)
        with self.patch_client("delete_job", return_value=True):
            first._mark_failed(RuntimeError("verifier down"))
        self.assertEqual(first.state, "failed")
        self.assertFalse(self.Batch._cron_enqueue_unchecked(limit=100))
        self.assertEqual(self.Batch.search_count([("source", "=", "recheck")]), 1)

        # ...and resumes once the back-off window has elapsed.
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.recheck_backoff_minutes", "1")
        first.invalidate_recordset()
        self.env.cr.execute(
            "UPDATE email_verification_batch SET write_date = now() - interval '2 hours' "
            "WHERE id = %s", (first.id,))
        first.invalidate_recordset()
        self.assertTrue(self.Batch._cron_enqueue_unchecked(limit=100))

    def test_swept_batch_is_submitted_by_the_same_cron_run(self):
        """The sweep cannot wake the cron (that would spin), so it must run
        before the submit step — otherwise every automatic batch waits a full
        interval between being queued and actually being sent."""
        self.env["res.partner"].create({"name": "A", "email": "auto@x.com"})
        with self.patch_client("create_job",
                               return_value={"job_id": "j-auto", "total": 1}), \
             self.patch_cron_progress():
            self.Batch._cron_process()

        batch = self.Batch.search([("source", "=", "recheck")], limit=1)
        self.assertTrue(batch, "the sweep queued nothing")
        self.assertEqual(batch.state, "running",
                         "the swept batch was queued but not sent in the same run")
        self.assertEqual(batch.job_id, "j-auto")

    def test_recheck_sweep_does_not_trigger_the_cron_it_runs_inside(self):
        """Triggering from inside the scheduled action would spin: enqueue ->
        trigger -> immediate run -> submit fails -> enqueue -> ..."""
        cron = self.env.ref("era_email_verification.ir_cron_ev_process")
        Trigger = self.env["ir.cron.trigger"].sudo()
        self.env["res.partner"].create({"name": "R", "email": "r@x.com"})
        before = Trigger.search_count([("cron_id", "=", cron.id)])
        self.assertTrue(self.Batch._cron_enqueue_unchecked(limit=100))
        self.assertEqual(Trigger.search_count([("cron_id", "=", cron.id)]), before)


@tagged("post_install", "-at_install")
class TestStaleRecheck(EVCommon):

    def _checked_partner(self, email, days_ago):
        partner = self.env["res.partner"].create({"name": email, "email": email})
        partner.with_context(ev_apply_result=True).write({
            "email_verification_status": "deliverable",
            "email_verification_score": 95,
            "email_verification_eligible": True,
            "email_verification_checked_email": email,
            "email_verification_checked_date": fields.Datetime.subtract(
                fields.Datetime.now(), days=days_ago),
            "email_verification_needs_recheck": False,
        })
        return partner

    def test_stale_days_selects_only_aged_checks(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.stale_days", "180")
        old = self._checked_partner("old@x.com", 400)
        recent = self._checked_partner("recent@x.com", 5)

        stale = self.env["res.partner"]._ev_find_stale(limit=100)
        self.assertIn(old, stale)
        self.assertNotIn(recent, stale)
        self.assertTrue(old.email_verification_is_stale)
        self.assertFalse(recent.email_verification_is_stale)

    def test_stale_days_setting_is_honoured(self):
        partner = self._checked_partner("aged@x.com", 40)
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.stale_days", "180")
        self.assertNotIn(partner, self.env["res.partner"]._ev_find_stale(limit=100))
        # Shorten the window: the same contact is now due for a re-check.
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.stale_days", "30")
        partner.invalidate_recordset()
        self.assertIn(partner, self.env["res.partner"]._ev_find_stale(limit=100))

    def test_stale_search_filter_round_trips(self):
        old = self._checked_partner("s-old@x.com", 400)
        recent = self._checked_partner("s-recent@x.com", 1)
        Partner = self.env["res.partner"]
        stale = Partner.search([("email_verification_is_stale", "=", True)])
        fresh = Partner.search([("email_verification_is_stale", "=", False)])
        self.assertIn(old, stale)
        self.assertNotIn(recent, stale)
        self.assertIn(recent, fresh)
        self.assertNotIn(old, fresh)


@tagged("post_install", "-at_install")
class TestStuckBatchValve(EVCommon):

    def _running(self, progress_hours_ago):
        batch = self.Batch.create({
            "state": "running", "source": "manual", "job_id": "j-stuck",
            "total_count": 1,
            "last_progress_at": fields.Datetime.subtract(
                fields.Datetime.now(), hours=progress_hours_ago),
        })
        partner = self.env["res.partner"].create({"name": "S", "email": "st@x.com"})
        self.env["email.verification.item"].create({
            "batch_id": batch.id, "email": "st@x.com", "partner_id": partner.id})
        return batch

    def test_batch_with_no_progress_is_failed_and_unblocks_the_queue(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.stuck_batch_hours", "6")
        stuck = self._running(progress_hours_ago=48)
        queued = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(
                partners=self.env["res.partner"].create(
                    {"name": "Q", "email": "q@x.com"})),
            source="partners")

        with self.patch_client("delete_job", return_value=True) as delete_mock, \
             self.patch_client("create_job",
                               return_value={"job_id": "j-next", "total": 1}), \
             self.patch_cron_progress():
            self.Batch._cron_process()

        self.assertEqual(stuck.state, "failed")
        self.assertIn("stuck", stuck.error_message.lower())
        delete_mock.assert_called()          # remote job cleaned up
        self.assertEqual(queued.state, "running")   # queue moves on

    def test_progressing_batch_is_never_failed(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.stuck_batch_hours", "6")
        healthy = self._running(progress_hours_ago=1)
        with self.patch_client("get_job", return_value={"status": "processing"}), \
             self.patch_client("get_results", return_value={"items": []}), \
             self.patch_cron_progress():
            self.Batch._cron_process()
        self.assertEqual(healthy.state, "running")

    def test_push_and_poll_both_record_progress(self):
        batch = self._running(progress_hours_ago=48)
        before = batch.last_progress_at
        batch._apply_pushed_results(
            [make_result("st@x.com", status="deliverable")], done=False)
        self.assertGreater(batch.last_progress_at, before)

    def test_transient_reconcile_failures_are_retried_before_cleanup(self):
        batch = self._running(progress_hours_ago=1)

        with self.patch_client("get_job", side_effect=UserError("temporary timeout")), \
             self.patch_client("delete_job", return_value=True) as delete_mock, \
             self.patch_cron_progress():
            self.Batch._cron_process()
            self.assertEqual(batch.state, "running")
            self.assertEqual(batch.poll_failure_count, 1)
            delete_mock.assert_not_called()

            self.Batch._cron_process()
            self.assertEqual(batch.state, "running")
            self.assertEqual(batch.poll_failure_count, 2)
            delete_mock.assert_not_called()

            self.Batch._cron_process()

        self.assertEqual(batch.state, "failed")
        self.assertEqual(batch.poll_failure_count, 3)
        delete_mock.assert_called_once_with(batch.job_id)


@tagged("post_install", "-at_install")
class TestCronTriggering(EVCommon):
    """The scheduled action is a fallback; the responsive path is _trigger().

    Its interval therefore must not double as the submit latency — without
    these triggers a queued batch would sit idle for a whole interval, and
    with one job in flight at a time a multi-batch list would stall an
    interval between every batch.
    """

    def _pending_triggers(self, cron):
        return self.env["ir.cron.trigger"].sudo().search_count(
            [("cron_id", "=", cron.id)])

    def test_cron_reports_remaining_work_before_doing_it(self):
        """_commit_progress returns a ~10s TIME budget, and a run that stops
        early having reported remaining == 0 is recorded FULLY_DONE, which
        reschedules a whole interval away. The cron must therefore declare
        outstanding work up front so an exhausted budget asks for an
        immediate re-run (PARTIALLY_DONE) instead."""
        calls = []

        def spy(cron_self, processed=0, **kwargs):
            calls.append(dict(kwargs, processed=processed))
            return float("inf")

        partner = self.env["res.partner"].create({"name": "B", "email": "b@x.com"})
        self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=partner), source="partners")

        with patch.object(type(self.env["ir.cron"]), "_commit_progress", spy), \
             self.patch_client("create_job",
                               return_value={"job_id": "j-rem", "total": 1}):
            self.Batch._cron_process()

        self.assertTrue(calls, "the cron never reported progress")
        # The very first call must declare the work, before any is done.
        self.assertIn("remaining", calls[0])
        self.assertGreater(calls[0]["remaining"], 0)
        # ...and a clean finish must wind it back down to zero.
        self.assertEqual(calls[-1].get("remaining"), 0)

    def test_batch_completed_by_push_is_not_failed_when_remote_job_is_gone(self):
        """import_offset is only advanced by the poll path, so a batch whose
        results all arrived by push still reads 0. Completeness must be judged
        from the items, or the reconcile would fail a perfectly good batch."""
        batch = self.Batch.create({
            "state": "running", "source": "manual", "job_id": "j-gone",
            "total_count": 1, "last_progress_at": fields.Datetime.now(),
        })
        partner = self.env["res.partner"].create({"name": "G", "email": "g@x.com"})
        self.env["email.verification.item"].create({
            "batch_id": batch.id, "email": "g@x.com", "partner_id": partner.id})
        # Everything imported by push: items done, but import_offset untouched.
        batch._apply_pushed_results([make_result("g@x.com")], done=False)
        self.assertEqual(batch.import_offset, 0)
        # Age the push so the reconcile poll picks the batch up.
        batch.last_push_at = fields.Datetime.subtract(
            fields.Datetime.now(), hours=1)

        # The verifier lost/purged the job before the terminal push landed.
        with self.patch_client("get_job", return_value=None), \
             self.patch_client("delete_job", return_value=True), \
             self.patch_cron_progress():
            self.Batch._cron_process()

        self.assertEqual(batch.state, "done")
        self.assertFalse(batch.error_message)

    def setUp(self):
        super().setUp()
        self.cron = self.env.ref("era_email_verification.ir_cron_ev_process")

    def test_cron_runs_quarter_hourly_as_a_fallback(self):
        self.assertEqual(self.cron.interval_number, 15)
        self.assertEqual(self.cron.interval_type, "minutes")

    def test_reconcile_window_stays_below_the_cron_interval(self):
        # Equal values make a batch wait two runs instead of one.
        self.assertLess(self.Batch._reconcile_stale_minutes(), 15)

    def test_enqueue_triggers_the_cron(self):
        before = self._pending_triggers(self.cron)
        partner = self.env["res.partner"].create({"name": "T", "email": "t@x.com"})
        self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=partner), source="partners")
        self.assertGreater(self._pending_triggers(self.cron), before)

    def test_enqueueing_nothing_does_not_trigger(self):
        before = self._pending_triggers(self.cron)
        self.assertFalse(self.Batch._enqueue_targets([], source="partners"))
        self.assertEqual(self._pending_triggers(self.cron), before)

    def test_push_completion_triggers_the_next_batch(self):
        """A push finalizes inside the webhook request, outside the cron, so
        nothing else would notice the queue just freed up."""
        batch = self.Batch.create({
            "state": "running", "source": "manual", "job_id": "j-trig",
            "total_count": 1, "last_progress_at": fields.Datetime.now(),
        })
        partner = self.env["res.partner"].create({"name": "P", "email": "p@x.com"})
        self.env["email.verification.item"].create({
            "batch_id": batch.id, "email": "p@x.com", "partner_id": partner.id})

        before = self._pending_triggers(self.cron)
        batch._apply_pushed_results(
            [make_result("p@x.com", status="deliverable")], done=True)
        self.assertEqual(batch.state, "done")
        self.assertGreater(self._pending_triggers(self.cron), before)

    def test_trigger_works_for_a_plain_verification_user(self):
        """ir.cron is readable by system admins only; the bulk action is not."""
        user = self.env["res.users"].create({
            "name": "EV User", "login": "ev_trigger_user",
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref(
                    "era_email_verification.group_email_verification_user").id,
            ])],
        })
        partner = self.env["res.partner"].create({"name": "U", "email": "u@x.com"})
        Batch = self.Batch.with_user(user)
        batches = Batch._enqueue_targets(
            Batch._prepare_targets(partners=partner), source="partners")
        self.assertTrue(batches)   # no AccessError on ir.cron


@tagged("post_install", "-at_install")
class TestRemoteJobCleanup(EVCommon):

    def _running_batch(self, job_id="j-clean"):
        batch = self.Batch.create({
            "state": "running", "source": "manual", "job_id": job_id,
            "total_count": 1, "last_progress_at": fields.Datetime.now(),
        })
        partner = self.env["res.partner"].create({"name": "C", "email": "c@x.com"})
        self.env["email.verification.item"].create({
            "batch_id": batch.id, "email": "c@x.com", "partner_id": partner.id})
        return batch

    def test_cancel_deletes_the_remote_job(self):
        batch = self._running_batch()
        with self.patch_client("delete_job", return_value=True) as delete_mock:
            batch.action_cancel()
        self.assertEqual(batch.state, "cancelled")
        delete_mock.assert_called_once()

    def test_failure_deletes_the_remote_job(self):
        batch = self._running_batch("j-fail")
        with self.patch_client("delete_job", return_value=True) as delete_mock:
            batch._mark_failed(RuntimeError("boom"))
        self.assertEqual(batch.state, "failed")
        delete_mock.assert_called_once()

    def test_reset_to_draft_clears_the_job_so_it_can_be_resubmitted(self):
        """A stale job_id made _submit and _submit_next_queued permanent
        no-ops (both guard on job_id being unset), stranding the batch."""
        batch = self._running_batch("j-reset")
        with self.patch_client("delete_job", return_value=True):
            batch.action_cancel()
        batch.action_reset_to_draft()
        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.job_id)
        self.assertEqual(batch.import_offset, 0)

        batch.action_queue()
        with self.patch_client("create_job",
                               return_value={"job_id": "j-again", "total": 1}):
            submitted = self.Batch._submit_next_queued()
        self.assertEqual(submitted, batch)
        self.assertEqual(batch.state, "running")
        self.assertEqual(batch.job_id, "j-again")
