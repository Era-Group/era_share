"""Pagination cursor + idempotent submit / re-import."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import EVCommon, make_result


@tagged("post_install", "-at_install")
class TestPaginationIdempotency(EVCommon):

    def _make_batch(self, n):
        partners = self.env["res.partner"].create(
            [{"name": "P%d" % i, "email": "p%d@example.com" % i} for i in range(n)])
        targets = self.Batch._prepare_targets(partners=partners)
        batch = self.Batch._enqueue_targets(targets, source="partners")
        with self.patch_client(
                "create_job",
                return_value={"job_id": "job", "status": "queued", "total": n}):
            batch._submit()
        return batch, partners

    def test_imports_across_multiple_pages(self):
        batch, partners = self._make_batch(3)
        all_items = [
            {"email": "p%d@example.com" % i, "state": "done",
             "result": make_result("p%d@example.com" % i, status="deliverable", score=90)}
            for i in range(3)]

        def fake_get_results(job_id, offset=0, limit=500):
            return {"items": all_items[offset:offset + limit]}

        with self.patch_client("get_results", side_effect=fake_get_results), \
             self.patch_client("delete_job", return_value=True) as delete_mock:
            imported = batch._import_available(page_limit=2)   # forces 2 pages

        self.assertEqual(imported, 3)
        self.assertEqual(batch.import_offset, 3)
        self.assertEqual(batch.state, "done")
        self.assertEqual(batch.deliverable_count, 3)
        delete_mock.assert_called_once()

    def test_submit_is_idempotent(self):
        batch, _ = self._make_batch(1)
        # already running with a job id -> a second submit must be a no-op
        with self.patch_client("create_job",
                               return_value={"job_id": "SHOULD_NOT", "total": 1}) as create_mock:
            batch._submit()
        create_mock.assert_not_called()
        self.assertEqual(batch.job_id, "job")

    def test_submit_retry_reuses_remote_key_and_callback_secret(self):
        partner = self.env["res.partner"].create({
            "name": "Retry", "email": "retry@example.com"})
        batch = self.Batch._enqueue_targets(
            self.Batch._prepare_targets(partners=partner), source="partners")
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.callback_base_url", "https://odoo.example.com")

        with self.patch_client(
                "create_job",
                side_effect=[UserError("timeout"), {"job_id": "job-retry", "total": 1}],
        ) as create_mock:
            try:
                batch._submit()
            except UserError:
                pass
            else:
                self.fail("The synthetic transport failure was not raised")
            first_secret = batch.callback_secret
            batch._submit()

        self.assertTrue(first_secret)
        self.assertEqual(batch.callback_secret, first_secret)
        first_call, second_call = create_mock.call_args_list
        self.assertEqual(
            first_call.kwargs["idempotency_key"],
            second_call.kwargs["idempotency_key"],
        )
        self.assertEqual(
            first_call.kwargs["callback_secret"],
            second_call.kwargs["callback_secret"],
        )

    def test_reimport_after_done_is_noop(self):
        batch, _ = self._make_batch(1)
        page = {"items": [{"email": "p0@example.com", "state": "done",
                           "result": make_result("p0@example.com", status="deliverable")}]}
        with self.patch_client("get_results", return_value=page), \
             self.patch_client("delete_job", return_value=True):
            batch._import_available()
        self.assertEqual(batch.state, "done")
        # a second import call does nothing (state != running)
        with self.patch_client("get_results", return_value=page) as gr:
            again = batch._import_available()
        self.assertEqual(again, 0)
        gr.assert_not_called()
