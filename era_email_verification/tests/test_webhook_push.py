"""Push webhook: idempotent apply, one-job-at-a-time serialization, the
incremental pull fallback, and the controller's HMAC / replay guards."""
import hashlib
import hmac
import json
import time

from odoo.tests import HttpCase, tagged

from .common import EVCommon, make_result


@tagged("post_install", "-at_install")
class TestPushApply(EVCommon):

    def _running_batch(self, emails, job_id="job-hook", secret="s" * 48):
        batch = self.Batch.create({
            "state": "running", "source": "manual", "job_id": job_id,
            "callback_secret": secret, "total_count": len(emails),
        })
        partners = self.env["res.partner"].create(
            [{"name": e, "email": e} for e in emails])
        self.env["email.verification.item"].create([
            {"batch_id": batch.id, "email": e, "partner_id": p.id}
            for e, p in zip(emails, partners)])
        return batch

    def test_apply_pushed_results_and_finalize(self):
        batch = self._running_batch(["a@x.com", "b@x.com"])
        chunk = [make_result("a@x.com", status="deliverable", score=95),
                 make_result("b@x.com", status="undeliverable", score=0)]
        imported = batch._apply_pushed_results(chunk, done=False)
        self.assertEqual(imported, 2)
        self.assertEqual(batch.state, "running")          # not done yet
        self.assertTrue(batch.last_push_at)
        self.assertTrue(self.blacklisted("b@x.com"))       # undeliverable

        # Terminal push finalizes locally. The remote DELETE is deliberately
        # NOT issued here: this runs inside the verifier's own webhook request,
        # and calling back out to it would hold that response open.
        with self.patch_client("delete_job", return_value=True) as delete_mock:
            batch._apply_pushed_results([], done=True)
        self.assertEqual(batch.state, "done")
        delete_mock.assert_not_called()
        self.assertTrue(batch.job_id, "job_id is kept for the cron to clean up")

        # ...the cron sweeps it instead.
        with self.patch_client("delete_job", return_value=True) as delete_mock, \
             self.patch_cron_progress():
            self.Batch._cron_process()
        delete_mock.assert_called_once()
        self.assertFalse(batch.job_id)

    def test_apply_pushed_results_is_idempotent(self):
        batch = self._running_batch(["a@x.com"])
        chunk = [make_result("a@x.com", status="deliverable")]
        self.assertEqual(batch._apply_pushed_results(chunk), 1)
        # a duplicate delivery (retry) imports nothing new
        self.assertEqual(batch._apply_pushed_results(chunk), 0)

    def test_terminal_push_does_not_finalize_missing_results(self):
        batch = self._running_batch(["a@x.com", "b@x.com"])

        imported = batch._apply_pushed_results(
            [make_result("a@x.com", status="deliverable")], done=True)

        self.assertEqual(imported, 1)
        self.assertEqual(batch.state, "running")
        self.assertTrue(batch.job_id)
        self.assertFalse(batch.last_push_at)
        pending = batch.item_ids.filtered(lambda item: item.email == "b@x.com")
        self.assertEqual(pending.state, "pending")

    def test_pull_does_not_advance_past_unknown_result(self):
        batch = self._running_batch(["a@x.com"])
        page = {"items": [{
            "email": "other@x.com", "state": "done",
            "result": make_result("other@x.com"),
        }]}

        with self.patch_client("get_results", return_value=page):
            imported = batch._import_available()

        self.assertEqual(imported, 0)
        self.assertEqual(batch.import_offset, 0)
        self.assertEqual(batch.state, "running")

    def test_one_job_in_flight_at_a_time(self):
        p1 = self.env["res.partner"].create({"name": "A", "email": "a@x.com"})
        p2 = self.env["res.partner"].create({"name": "B", "email": "b@x.com"})
        b1 = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=p1), source="partners")
        b2 = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=p2), source="partners")

        with self.patch_client("create_job",
                               return_value={"job_id": "j1", "total": 1}):
            submitted = self.Batch._submit_next_queued()
        self.assertEqual(submitted, b1)
        self.assertEqual(b1.state, "running")
        self.assertEqual(b2.state, "queued")

        # a job is running -> nothing new is submitted
        self.assertFalse(self.Batch._submit_next_queued())
        self.assertEqual(b2.state, "queued")

        # once the first cycle is done, the next one submits
        b1.write({"state": "done"})
        with self.patch_client("create_job",
                               return_value={"job_id": "j2", "total": 1}):
            self.Batch._submit_next_queued()
        self.assertEqual(b2.state, "running")

    def test_fallback_imports_completed_prefix_only(self):
        partners = self.env["res.partner"].create(
            [{"name": "P%d" % i, "email": "p%d@x.com" % i} for i in range(3)])
        batch = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=partners), source="partners")
        with self.patch_client("create_job",
                               return_value={"job_id": "jf", "total": 3}):
            batch._submit()

        def slicer(rows):
            def fake(job_id, offset=0, limit=500):
                return {"items": rows[offset:offset + limit]}
            return fake

        # p1 still processing -> only the p0 prefix imports, no finalize
        partial = [
            {"email": "p0@x.com", "state": "done", "result": make_result("p0@x.com")},
            {"email": "p1@x.com", "state": "processing", "result": None},
            {"email": "p2@x.com", "state": "done", "result": make_result("p2@x.com")},
        ]
        with self.patch_client("get_results", side_effect=slicer(partial)), \
             self.patch_client("delete_job", return_value=True) as delete_mock:
            imported = batch._import_available()
        self.assertEqual(imported, 1)
        self.assertEqual(batch.import_offset, 1)
        self.assertEqual(batch.state, "running")
        delete_mock.assert_not_called()

        # everything done now -> the rest imports and the batch finalizes
        full = [
            {"email": "p0@x.com", "state": "done", "result": make_result("p0@x.com")},
            {"email": "p1@x.com", "state": "done", "result": make_result("p1@x.com")},
            {"email": "p2@x.com", "state": "done", "result": make_result("p2@x.com")},
        ]
        with self.patch_client("get_results", side_effect=slicer(full)), \
             self.patch_client("delete_job", return_value=True) as delete_mock:
            batch._import_available()
        self.assertEqual(batch.import_offset, 3)
        self.assertEqual(batch.state, "done")
        delete_mock.assert_called_once()


@tagged("post_install", "-at_install")
class TestWebhookController(HttpCase):

    def setUp(self):
        super().setUp()
        self.secret = "w" * 48
        self.batch = self.env["email.verification.batch"].create({
            "state": "running", "source": "manual", "job_id": "job-http",
            "callback_secret": self.secret, "total_count": 1,
        })
        partner = self.env["res.partner"].create({"name": "H", "email": "h@x.com"})
        self.env["email.verification.item"].create({
            "batch_id": self.batch.id, "email": "h@x.com", "partner_id": partner.id})
        self.url = "/era_email_verification/webhook"

    def _post(self, body_dict, secret=None, timestamp=None):
        body = json.dumps(body_dict).encode("utf-8")
        ts = timestamp if timestamp is not None else str(int(time.time()))
        sig = "sha256=" + hmac.new(
            (secret or self.secret).encode(), ts.encode() + b"." + body,
            hashlib.sha256).hexdigest()
        return self.url_open(self.url, data=body, headers={
            "Content-Type": "application/json",
            "X-EV-Timestamp": ts, "X-EV-Signature": sig})

    def test_valid_push_applies(self):
        resp = self._post({"job_id": "job-http", "done": True,
                           "items": [make_result("h@x.com", status="deliverable")]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))
        self.batch.invalidate_recordset()
        self.assertEqual(self.batch.state, "done")

    def test_bad_signature_rejected(self):
        resp = self._post({"job_id": "job-http", "items": []}, secret="wrong" * 10)
        self.assertEqual(resp.status_code, 401)
        self.batch.invalidate_recordset()
        self.assertEqual(self.batch.state, "running")

    def test_stale_timestamp_rejected(self):
        resp = self._post({"job_id": "job-http", "items": []},
                          timestamp=str(int(time.time()) - 4000))
        self.assertEqual(resp.status_code, 401)

    def test_unknown_job_gives_same_401_as_bad_sig(self):
        # No 404 oracle: unknown job looks identical to a bad signature.
        resp = self._post({"job_id": "does-not-exist", "items": []})
        self.assertEqual(resp.status_code, 401)

    def test_non_ascii_signature_rejected_cleanly(self):
        # A non-ASCII signature must be a clean 401, never a 500.
        body = json.dumps({"job_id": "job-http", "items": []}).encode()
        ts = str(int(time.time()))
        resp = self.url_open(self.url, data=body, headers={
            "Content-Type": "application/json", "X-EV-Timestamp": ts,
            "X-EV-Signature": "sha256=café"})
        self.assertEqual(resp.status_code, 401)

    def test_malformed_item_is_ignored_not_500(self):
        # A correctly-signed push carrying a junk (non-dict) item -> 200, skipped.
        resp = self._post({"job_id": "job-http", "done": False,
                           "items": ["oops", 123, {"email": "h@x.com",
                                                    "status": "deliverable", "score": 90}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("imported"), 1)
