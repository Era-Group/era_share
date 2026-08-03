"""Asynchronous verification batch + the scheduled worker that drives it.

Lifecycle: ``draft -> queued -> running -> done`` (or ``failed`` / ``cancelled``).
The ``ir.cron`` submits queued batches, polls running ones, and imports result
pages in bounded, idempotent steps using the Odoo 19 ``_commit_progress`` cron
API so it always returns before its time budget is exhausted.

Delivery: the verifier PUSHES completed results to an Odoo webhook (HMAC-signed)
in chunks as they finish; a low-frequency reconcile cron PULLS as a safety net
for anything a push missed. Either path imports incrementally and idempotently.

Idempotency:
* a batch is submitted only while it has no ``job_id`` (retry never creates a
  second remote job);
* only ONE job is in flight at a time (a new list is not submitted until the
  previous cycle is fully imported);
* results are applied at most once per item (items already imported are
  skipped), and the pull fallback advances only over the contiguous
  completed-prefix via ``import_offset``;
* the remote job is deleted only after every result is imported and reconciled.
"""
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from . import constants

_logger = logging.getLogger(__name__)


def _norm(email):
    # Strip FIRST: a leading/trailing non-breaking space (U+00A0) is returned
    # unchanged-and-truthy by email_normalize(), so without stripping it here an
    # item's normalized email differs from its partner's, the write-back is
    # marked stale, and the sweep re-verifies the same address indefinitely
    # (observed as a 6000-batch storm re-probing one domain). .strip() removes
    # unicode whitespace including NBSP.
    e = (email or "").strip()
    return email_normalize(e) or e.lower()


class EmailVerificationBatch(models.Model):
    _name = "email.verification.batch"
    _description = "Email Verification Batch"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(string="Reference", default="New", copy=False, readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        string="State", default="draft", required=True, tracking=True, index=True)
    source = fields.Selection(
        [
            ("manual", "Manual selection"),
            ("partners", "Contacts sweep"),
            ("mailing", "Mailing list"),
            ("recheck", "Automatic re-check"),
        ],
        string="Source", default="manual")

    job_id = fields.Char(string="Remote Job ID", copy=False, index=True, readonly=True)
    check_smtp = fields.Boolean(string="Probe SMTP", default=True)
    check_catch_all = fields.Boolean(string="Detect catch-all", default=True)
    # Per-batch secret the verifier signs its result pushes with (system-only).
    callback_secret = fields.Char(
        string="Callback secret", copy=False, readonly=True,
        groups="base.group_system")
    last_push_at = fields.Datetime(
        string="Last result push", copy=False, readonly=True,
        help="When the verifier last pushed results for this batch. Used to "
             "decide when the reconcile poll should step in as a fallback.")
    last_progress_at = fields.Datetime(
        string="Last progress", copy=False, readonly=True,
        help="Set on submit and bumped every time a push or a poll actually "
             "imports at least one new result. If this stays stale for too "
              "long the batch is treated as stuck (see the stuck-batch "
              "safety valve) so a hung remote job can never block the queue.")
    poll_failure_count = fields.Integer(
        string="Consecutive reconcile failures", copy=False, readonly=True,
        help="Transient polling/import failures are retried before the batch "
             "is failed and its remote job is deleted.")

    total_count = fields.Integer(string="Total", readonly=True)
    import_offset = fields.Integer(string="Import cursor", default=0, copy=False, readonly=True)
    imported_count = fields.Integer(string="Imported", readonly=True)
    deliverable_count = fields.Integer(string="Deliverable", readonly=True)
    undeliverable_count = fields.Integer(string="Undeliverable", readonly=True)
    risky_count = fields.Integer(string="Risky", readonly=True)
    unknown_count = fields.Integer(string="Unknown", readonly=True)
    stale_count = fields.Integer(string="Stale", readonly=True)
    blacklist_added_count = fields.Integer(string="Blacklisted", readonly=True)
    progress = fields.Float(string="Progress %", compute="_compute_progress")

    company_id = fields.Many2one(
        "res.company", string="Company", required=True, index=True,
        default=lambda self: self.env.company)
    user_id = fields.Many2one(
        "res.users", string="Created by", default=lambda self: self.env.user, readonly=True)
    date_submitted = fields.Datetime(string="Submitted on", readonly=True, copy=False)
    date_done = fields.Datetime(string="Completed on", readonly=True, copy=False)
    error_message = fields.Text(string="Error", readonly=True, copy=False)

    item_ids = fields.One2many("email.verification.item", "batch_id", string="Items")
    item_count = fields.Integer(string="Items", compute="_compute_item_count")

    # -- computes -------------------------------------------------------------
    @api.depends("total_count", "imported_count")
    def _compute_progress(self):
        for batch in self:
            batch.progress = (
                100.0 * batch.imported_count / batch.total_count
                if batch.total_count else 0.0)

    def _compute_item_count(self):
        data = self.env["email.verification.item"]._read_group(
            [("batch_id", "in", self.ids)], ["batch_id"], ["__count"])
        counts = {batch.id: count for batch, count in data}
        for batch in self:
            batch.item_count = counts.get(batch.id, 0)

    # -- create ---------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.name or rec.name == "New":
                rec.name = "EV%05d" % rec.id
        return records

    # -- config accessors -----------------------------------------------------
    @api.model
    def _icp(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(
            "era_email_verification." + key, default)

    @api.model
    def _batch_size(self):
        try:
            size = int(self._icp("batch_size", 5000) or 5000)
        except (TypeError, ValueError):
            size = 5000
        return max(1, min(size, 5000))

    @api.model
    def _icp_bool(self, key, default=False):
        return str(self._icp(key, default)).strip().lower() in (
            "1", "true", "yes", "on")

    @api.model
    def _blacklist_policy(self):
        """Read the auto-blacklist checkboxes into an outcome -> bool mapping."""
        return {
            outcome: self._icp_bool(
                "blacklist_" + outcome,
                constants.DEFAULT_BLACKLIST_OUTCOMES[outcome])
            for outcome in constants.BLACKLIST_OUTCOMES
        }

    @api.model
    def _min_eligible_score(self):
        try:
            return int(self._icp("min_eligible_score", 80) or 80)
        except (TypeError, ValueError):
            return 80

    @api.model
    def _push_enabled(self):
        return str(self._icp("push_enabled", "True")).lower() in (
            "1", "true", "yes", "on")

    @api.model
    def _reconcile_stale_minutes(self):
        """How long a batch's push must have been silent before the fallback
        poll takes over. Must stay comfortably BELOW the cron interval: if the
        two are equal, a batch whose push died just after a tick is still one
        second short of the window at the next tick, so it consistently waits
        two ticks instead of one."""
        try:
            return max(1, int(self._icp("reconcile_stale_minutes", 5) or 5))
        except (TypeError, ValueError):
            return 5

    @api.model
    def _stale_days(self):
        try:
            return max(1, int(self._icp("stale_days", 180) or 180))
        except (TypeError, ValueError):
            return 180

    @api.model
    def _trigger_cron(self):
        """Ask the scheduler to run ``_cron_process`` as soon as it can.

        The cron interval is only a safety net (see its description): the
        latency-sensitive work — submitting a batch the moment it is queued,
        and starting the next one the moment a batch completes — is driven by
        this trigger instead. Without it the interval would have to double as
        the submit latency, so a user clicking *Verify Email(s)* would wait a
        full interval before anything happened at all.

        ``sudo`` because ``ir.cron`` is readable by system administrators
        only, while this runs for any Email Verification user.
        """
        cron = self.env.ref(
            "era_email_verification.ir_cron_ev_process", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    @api.model
    def _recheck_backoff_minutes(self):
        """How long the auto re-check sweep stands down after a failed
        recheck batch, so an unreachable verifier cannot make it manufacture
        an identical failed batch on every run."""
        try:
            return max(1, int(self._icp("recheck_backoff_minutes", 60) or 60))
        except (TypeError, ValueError):
            return 60

    @api.model
    def _stuck_after_hours(self):
        try:
            return max(1, int(self._icp("stuck_batch_hours", 6) or 6))
        except (TypeError, ValueError):
            return 6

    @api.model
    def _webhook_url(self):
        """Public https URL the verifier should push results to, or False.

        Uses the configured override, else Odoo's own ``web.base.url``. Push is
        only offered over https (the verifier refuses anything else)."""
        base = (self._icp("callback_base_url", "") or "").strip()
        if not base:
            base = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").strip()
        base = base.rstrip("/")
        if not base.lower().startswith("https://"):
            return False
        return base + "/era_email_verification/webhook"

    # -- enqueue helpers ------------------------------------------------------
    @api.model
    def _prepare_targets(self, partners=None, mailing_contacts=None):
        """De-duplicate a selection by normalized email into a target list.

        Each unique address becomes ONE item, remembering the first partner
        and/or mailing contact it was found on. Addresses the verifier's API
        would reject outright are dropped here — see ``_is_submittable``.
        """
        targets = {}
        skipped = []
        for record, key in ((partners or self.env["res.partner"], "partner_id"),
                            (mailing_contacts or self.env["mailing.contact"],
                             "mailing_contact_id")):
            for row in record:
                raw = (row.email or "").strip()
                norm = _norm(raw)
                if not norm:
                    continue
                if not self._is_submittable(raw):
                    skipped.append(raw)
                    continue
                t = targets.setdefault(
                    norm, {"email": raw, "partner_id": False,
                           "mailing_contact_id": False})
                t[key] = t[key] or row.id
        if skipped:
            _logger.info(
                "Email Verification: skipped %d address(es) the verifier API "
                "cannot accept (e.g. %r).", len(skipped), skipped[:5])
        return list(targets.values())

    @api.model
    def _is_submittable(self, email):
        """Would the verifier's /v1/jobs accept this address in a payload?

        Its request model rejects the WHOLE job with HTTP 422 if a single
        entry falls outside 3..320 characters, so junk sitting in an email
        field (".", "00", "Mm" …) would otherwise fail every batch it lands
        in — permanently, since the re-check sweep keeps reselecting the same
        addresses. Mirror that one rule here and drop only what it refuses.

        Deliberately NOT a validity check: a syntactically broken but
        plausibly-sized address like "not-an-email" is still submitted, and
        the verifier answers 'undeliverable', which is useful data quality
        signal.
        """
        return constants.MIN_EMAIL_LENGTH <= len(email or "") <= constants.MAX_EMAIL_LENGTH

    @api.model
    def _enqueue_targets(self, targets, check_smtp=None, check_catch_all=None,
                         source="manual", trigger=True):
        """Create queued batches of at most ``batch_size`` unique addresses.

        ``trigger=False`` suppresses the wake-up call to the scheduled action.
        Callers that are themselves running inside that action must pass it:
        the batch they just queued is picked up by the next run anyway, and
        triggering from within would make a failing submit spin (enqueue ->
        trigger -> immediate run -> submit fails -> enqueue -> ...).
        """
        if check_smtp is None:
            check_smtp = str(self._icp("default_check_smtp", "True")).lower() in (
                "1", "true", "yes", "on")
        if check_catch_all is None:
            check_catch_all = str(self._icp("default_check_catch_all", "True")).lower() in (
                "1", "true", "yes", "on")
        size = self._batch_size()
        Item = self.env["email.verification.item"]
        batches = self.browse()
        for start in range(0, len(targets), size):
            chunk = targets[start:start + size]
            batch = self.create({
                "check_smtp": check_smtp,
                "check_catch_all": check_catch_all,
                "source": source,
                "state": "queued",
                "total_count": len(chunk),
            })
            Item.create([{
                "batch_id": batch.id,
                "email": t["email"],
                "partner_id": t["partner_id"],
                "mailing_contact_id": t["mailing_contact_id"],
            } for t in chunk])
            batches |= batch
        if batches and trigger:
            # Start verifying now rather than at the next scheduled run.
            self._trigger_cron()
        return batches

    # -- buttons --------------------------------------------------------------
    def action_queue(self):
        queued = self.filtered(lambda b: b.state == "draft")
        queued.write({"state": "queued"})
        if queued:
            self._trigger_cron()

    def action_cancel(self):
        to_cancel = self.filtered(
            lambda b: b.state in ("draft", "queued", "running", "failed"))
        for batch in to_cancel:
            batch._delete_remote_job()
        to_cancel.write({"state": "cancelled"})

    def action_reset_to_draft(self):
        """Reset a failed/cancelled batch to draft for a clean retry.

        Clears ``job_id`` (and the other per-job bookkeeping) too: a stale
        ``job_id`` would otherwise make ``_submit`` a permanent no-op and
        ``_submit_next_queued`` never pick this batch up again (both guard
        on ``job_id`` being unset), silently stranding it forever.
        """
        resettable = self.filtered(lambda b: b.state in ("failed", "cancelled"))
        for batch in resettable:
            batch._delete_remote_job()
        resettable.write({
            "state": "draft", "error_message": False,
            "job_id": False, "import_offset": 0,
            "last_push_at": False, "last_progress_at": False,
            "poll_failure_count": 0,
        })

    def action_view_items(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Verification Items"),
            "res_model": "email.verification.item",
            "view_mode": "list,form",
            "domain": [("batch_id", "=", self.id)],
            "context": {"default_batch_id": self.id, "search_default_group_status": 1},
        }

    def _mark_failed(self, exc):
        self.ensure_one()
        _logger.warning("Verification batch %s failed: %s", self.name, type(exc).__name__)
        self._delete_remote_job()
        self.write({"state": "failed", "error_message": str(exc)[:2000]})

    def _delete_remote_job(self):
        """Best-effort DELETE of the remote job; local data is safe either
        way. Shared by cancel/fail/reset so a job never lingers on the
        verifier once Odoo has given up on its batch."""
        self.ensure_one()
        if not self.job_id:
            return
        try:
            self.env["email.verification.client"].delete_job(self.job_id)
        except Exception:  # noqa: BLE001 - local data is already safe
            _logger.info("Could not delete remote job for %s (already gone?).", self.name)

    # -- submit / poll / import ----------------------------------------------
    def _idempotency_key(self):
        """Stable remote-create key for this database and batch."""
        self.ensure_one()
        database_uuid = self.env["ir.config_parameter"].sudo().get_param(
            "database.uuid") or self.env.cr.dbname
        return "%s:email-verification:%s" % (database_uuid, self.id)

    def _submit(self):
        """POST the remote job. Idempotent: only a queued batch with no job."""
        self.ensure_one()
        if self.job_id or self.state != "queued":
            return
        emails = self.item_ids.mapped("email")
        if not emails:
            self.write({"state": "done", "date_done": fields.Datetime.now()})
            return
        # Offer a result-push webhook when enabled and a public https URL exists;
        # polling still reconciles regardless.
        callback_url = callback_secret = None
        if self._push_enabled():
            callback_url = self._webhook_url()
            if callback_url:
                callback_secret = self.callback_secret or secrets.token_hex(24)
                # Persist before the request. A transport timeout after the
                # service accepted the job must retry with the same signature
                # secret as well as the same idempotency key.
                if not self.callback_secret:
                    self.callback_secret = callback_secret
        resp = self.env["email.verification.client"].create_job(
            emails, self.check_smtp, self.check_catch_all,
            callback_url=callback_url, callback_secret=callback_secret,
            idempotency_key=self._idempotency_key())
        if not isinstance(resp, dict) or not resp.get("job_id"):
            raise UserError(_("The Email Verifier returned an invalid job response."))
        try:
            remote_total = int(resp.get("total", len(emails)))
        except (TypeError, ValueError):
            raise UserError(_("The Email Verifier returned an invalid job total."))
        if remote_total < 1 or remote_total > len(emails):
            raise UserError(_("The Email Verifier returned an invalid job total."))
        now = fields.Datetime.now()
        self.write({
            "job_id": resp.get("job_id"),
            "total_count": remote_total,
            "callback_secret": callback_secret,
            "state": "running",
            "date_submitted": now,
            "last_progress_at": now,
            "poll_failure_count": 0,
            "error_message": False,
        })

    def _poll(self):
        """Return the remote job status ('queued'|'processing'|'done') or None."""
        self.ensure_one()
        if not self.job_id:
            return None
        job = self.env["email.verification.client"].get_job(self.job_id)
        return (job or {}).get("status") if job else None

    def _import_available(self, page_limit=500, max_pages=None):
        """Pull fallback: import the contiguous completed-prefix, incrementally.

        ``import_offset`` advances only over items the verifier reports as
        ``done``, so an item still processing is never skipped and results are
        never lost. Re-applying an already-imported item is a no-op, so this is
        safe to run alongside the push path.
        """
        self.ensure_one()
        if self.state != "running" or not self.job_id:
            return 0
        client = self.env["email.verification.client"]
        policy = self._blacklist_policy()
        min_score = self._min_eligible_score()
        imported = pages = 0
        while self.import_offset < self.total_count:
            page = client.get_results(
                self.job_id, offset=self.import_offset, limit=page_limit)
            api_items = (page or {}).get("items") or []
            if not api_items:
                break
            by_email = {_norm(i.email): i for i in self.item_ids}
            advanced = 0
            advance_blocked = False
            for api_item in api_items:
                if api_item.get("state") != "done":
                    break  # first not-yet-completed item — stop advancing
                item = by_email.get(_norm(api_item.get("email")))
                result = api_item.get("result")
                if not item or not isinstance(result, dict):
                    advance_blocked = True
                    continue  # apply later valid items, but never skip this one
                already_imported = item.state in (
                    constants.ITEM_DONE, constants.ITEM_STALE)
                applied = self._apply_one_result(
                    item, result, policy, min_score)
                imported += applied
                if not already_imported and not applied:
                    advance_blocked = True
                elif not advance_blocked:
                    advanced += 1
            self.import_offset += advanced
            pages += 1
            if advanced < len(api_items):
                break  # hit a pending item inside this page
            if len(api_items) < page_limit:
                break  # last page and fully done
            if max_pages and pages >= max_pages:
                break
        if imported:
            self.last_progress_at = fields.Datetime.now()
        self._recompute_counts()
        if (self.import_offset >= self.total_count and self.total_count
                and self._all_items_imported()):
            self._finalize()
        return imported

    def _all_items_imported(self):
        self.ensure_one()
        return all(item.state in (constants.ITEM_DONE, constants.ITEM_STALE)
                   for item in self.item_ids)

    def _finalize(self, purge=True):
        """Mark the batch done, deleting the remote job unless deferred.

        ``purge=False`` is used by the push path: that runs inside the
        verifier's own webhook request, and calling straight back out to the
        verifier would hold its response open (risking its callback timeout,
        a pointless retry, and a slow webhook). The batch keeps its
        ``job_id`` and the cron's cleanup step deletes it instead. The poll
        path already runs in the cron, so it purges inline as before.
        """
        self.ensure_one()
        if purge:
            self._delete_remote_job()
            self.job_id = False
        self.write({"state": "done", "date_done": fields.Datetime.now()})

    # -- result application ---------------------------------------------------
    def _apply_one_result(self, item, result, policy, min_score):
        """Apply one result dict onto an item + its targets. Idempotent: an item
        already imported (done/stale) is skipped. Returns 1 if newly applied.

        Any failure while applying a single item (the known invalid-address
        case is already guarded in ``_write_targets``; this is the backstop
        for anything else) is caught and recorded on that item alone — one bad
        address must never sink the rest of the page/push.
        """
        if not item or not result or item.state in (
                constants.ITEM_DONE, constants.ITEM_STALE):
            return 0
        Item = self.env["email.verification.item"]
        vals = Item._normalize_result(result)
        flags = vals.pop("_flags")
        try:
            with self.env.cr.savepoint():
                item.write(dict(
                    vals, state=constants.ITEM_DONE,
                    attempts=item.attempts + 1))
                self._write_targets(item, vals, flags, policy, min_score)
            return 1
        except Exception as exc:  # noqa: BLE001 - isolate to this item only
            _logger.warning(
                "Email Verification: failed to apply result for %r in batch "
                "%s (%s)", item.email, self.name, type(exc).__name__)
            item.write({
                "state": constants.ITEM_ERROR,
                "error_message": str(exc)[:2000],
                "attempts": item.attempts + 1,
            })
            return 0

    def _apply_pushed_results(self, items, done=False):
        """Apply a pushed chunk of result dicts (idempotently); finalize when the
        push signals completion. Returns the number newly imported.

        Called by the webhook controller after it has verified the signature.
        """
        self.ensure_one()
        imported = 0
        if self.state == "running":
            policy = self._blacklist_policy()
            min_score = self._min_eligible_score()
            by_email = {_norm(i.email): i for i in self.item_ids}
            for result in items or []:
                if not isinstance(result, dict):
                    continue  # ignore malformed elements rather than 500
                imported += self._apply_one_result(
                    by_email.get(_norm(result.get("email"))), result,
                    policy, min_score)
            now = fields.Datetime.now()
            self.last_push_at = now
            if imported:
                self.last_progress_at = now
            self._recompute_counts()
        if done and self.state == "running":
            if self._all_items_imported():
                self._finalize(purge=False)
            else:
                # A terminal callback may arrive after a chunk was lost or
                # delivered out of order. Keep the remote job and force an
                # immediate pull reconciliation instead of losing results.
                self.last_push_at = False
            # This ran in the verifier's webhook request, outside the cron, so
            # nothing else will notice the queue just freed up. Wake the cron
            # to submit the next batch and purge this one's remote job — the
            # poll path needs no trigger, it is already inside that cron run.
            self._trigger_cron()
        return imported

    def _write_targets(self, item, vals, flags, policy, min_score):
        """Apply a result to the partner / mailing contact, guarding the
        email-change race, and blacklist per policy."""
        snapshot = _norm(item.email)
        status = vals["status"]
        stale = False

        partner_vals = {
            "email_verification_status": status,
            "email_verification_score": vals["score"],
            "email_verification_checked_email": item.email,
            "email_verification_checked_date": vals["checked_at"] or fields.Datetime.now(),
            "email_verification_reason": vals["reason_summary"],
            "email_verification_catch_all": vals["catch_all"],
            "email_verification_disposable": vals["disposable"],
            "email_verification_role": vals["role_account"],
            "email_verification_free_provider": vals["free_provider"],
            "email_verification_smtp_code": vals["smtp_code"],
            "email_verification_eligible": constants.is_eligible(
                status, vals["score"], flags, min_score),
            "email_verification_needs_recheck": False,
        }
        if item.partner_id:
            if _norm(item.partner_id.email) == snapshot:
                item.partner_id.with_context(ev_apply_result=True).write(partner_vals)
            else:
                stale = True

        if item.mailing_contact_id:
            if _norm(item.mailing_contact_id.email) == snapshot:
                item.mailing_contact_id.with_context(ev_apply_result=True).write({
                    "email_verification_status": status,
                    "email_verification_score": vals["score"],
                    "email_verification_checked_email": item.email,
                    "email_verification_checked_date":
                        vals["checked_at"] or fields.Datetime.now(),
                    "email_verification_reason": vals["reason_summary"],
                    "email_verification_eligible": constants.is_eligible(
                        status, vals["score"], flags, min_score),
                    "email_verification_needs_recheck": False,
                })
            else:
                stale = True

        if stale:
            item.state = constants.ITEM_STALE

        if constants.should_blacklist(policy, status, flags):
            # mail.blacklist.create() raises UserError for anything
            # email_normalize() can't parse (e.g. "not-an-email" — exactly the
            # kind of address that legitimately verifies as undeliverable).
            # Skip it rather than let one bad address sink the whole import.
            if email_normalize(item.email):
                self.env["mail.blacklist"].sudo()._add(
                    item.email,
                    message=_("Auto-blacklisted by Email Verification "
                              "(status: %s, batch %s).") % (status, self.name))
                item.blacklisted = True
            else:
                _logger.info(
                    "Email Verification: %r (batch %s) is not a normalizable "
                    "address; skipped auto-blacklisting.", item.email, self.name)

    def _recompute_counts(self):
        for batch in self:
            items = batch.item_ids
            batch.deliverable_count = len(items.filtered(
                lambda i: i.status == constants.STATUS_DELIVERABLE))
            batch.undeliverable_count = len(items.filtered(
                lambda i: i.status == constants.STATUS_UNDELIVERABLE))
            batch.risky_count = len(items.filtered(
                lambda i: i.status == constants.STATUS_RISKY))
            batch.unknown_count = len(items.filtered(
                lambda i: i.status == constants.STATUS_UNKNOWN))
            batch.stale_count = len(items.filtered(
                lambda i: i.state == constants.ITEM_STALE))
            batch.blacklist_added_count = len(items.filtered("blacklisted"))
            batch.imported_count = len(items.filtered(
                lambda i: i.state in (constants.ITEM_DONE, constants.ITEM_STALE)))

    # -- scheduled worker -----------------------------------------------------
    @api.model
    def _cron_process(self):
        """Reconcile push-stale running batches (pull fallback), then submit the
        next queued batch — but only ONE job in flight at a time."""
        Cron = self.env["ir.cron"]

        stuck_cutoff = fields.Datetime.subtract(
            fields.Datetime.now(), hours=self._stuck_after_hours())
        stuck = self.search([
            ("state", "=", "running"), ("last_progress_at", "<", stuck_cutoff),
        ])
        cutoff = fields.Datetime.subtract(
            fields.Datetime.now(), minutes=self._reconcile_stale_minutes())
        stale = self.search([
            ("state", "=", "running"),
            "|", ("last_push_at", "=", False), ("last_push_at", "<", cutoff),
        ])

        # Declare the outstanding work BEFORE doing any of it. What
        # ``_commit_progress`` returns is this run's remaining *time* budget —
        # ir.cron.MIN_TIME_PER_JOB, only 10 seconds — and a run that stops
        # early while still reporting ``remaining == 0`` is recorded as
        # FULLY_DONE, which reschedules by a whole interval. Importing a page
        # of results easily exceeds 10 seconds, so without this the steps
        # below (submitting the next batch above all) would be skipped and
        # then parked for the full interval. Reporting real remaining work
        # makes an early return PARTIALLY_DONE instead, and Odoo re-runs us
        # immediately. The +1 covers the submit step.
        Cron._commit_progress(remaining=len(stuck) + len(stale) + 1)

        # 1) Safety valve: a running batch that hasn't made ANY progress (no
        #    push, no successful poll import) since it was submitted, for
        #    longer than the stuck-batch window, is failed outright. Without
        #    this a hung/misbehaving remote job — one that still answers
        #    polls but never actually finishes an item — would block the
        #    one-job-at-a-time queue forever. A batch that IS progressing,
        #    even slowly on a huge list, is left alone: only the total
        #    absence of progress trips this.
        for batch in stuck:
            batch._mark_failed(RuntimeError(
                "No progress for over %d hour(s); the remote job may be "
                "stuck." % self._stuck_after_hours()))
            if Cron._commit_progress(processed=1) <= 0:
                return

        # 2) Fallback: pull for running batches the verifier hasn't pushed to
        #    recently (push down, Odoo was unreachable, or push-less poll mode).
        for batch in stale:
            if batch.state != "running":
                # Both lists were searched before step 1 ran (so the totals
                # declared to _commit_progress are accurate), which means a
                # batch the stuck valve just failed can still be sitting in
                # this one. Polling it would overwrite the real reason it was
                # failed with whatever the now-pointless request returns.
                if Cron._commit_progress(processed=1) <= 0:
                    return
                continue
            try:
                status = batch._poll()
                if status is None and batch.job_id:
                    # The remote job is gone. Judge completeness by the items
                    # themselves, NOT by import_offset: that cursor is only
                    # advanced by this poll path, so a batch whose results all
                    # arrived by push still reads 0 and would be failed here
                    # even though every result is safely imported.
                    if batch._all_items_imported():
                        batch.write({"state": "done", "date_done": fields.Datetime.now()})
                    else:
                        batch._mark_failed(RuntimeError("Remote job no longer exists"))
                else:
                    batch._import_available(page_limit=500, max_pages=4)
                    if batch.poll_failure_count or batch.error_message:
                        batch.write({
                            "poll_failure_count": 0,
                            "error_message": False,
                        })
            except Exception as exc:  # noqa: BLE001
                failures = batch.poll_failure_count + 1
                if failures >= 3:
                    batch.poll_failure_count = failures
                    batch._mark_failed(exc)
                else:
                    _logger.warning(
                        "Verification batch %s reconcile failed (%d/3): %s",
                        batch.name, failures, type(exc).__name__)
                    batch.write({
                        "poll_failure_count": failures,
                        "error_message": str(exc)[:2000],
                    })
            if Cron._commit_progress(processed=1) <= 0:
                return

        # 3) Optional auto re-check sweep. Deliberately BEFORE the submit step:
        #    the sweep cannot wake the cron itself (triggering from inside the
        #    run it is part of would spin), so a batch queued here after the
        #    submit step would sit untouched until the next scheduled run —
        #    adding a full interval of dead time to every automatic batch.
        #    Queuing first lets the same run send it.
        if self._icp_bool("auto_recheck", False):
            self._cron_enqueue_unchecked(limit=self._batch_size())

        # 4) Serialize submission: never start a new job while one is running.
        self._submit_next_queued()
        # Consume the submit slot unconditionally, so the seeded count above
        # reaches zero on a run that completes normally.
        budget_left = Cron._commit_progress(processed=1)

        # 5) Delete remote jobs for batches that finished without a
        #    synchronous delete (see ``_finalize``'s docstring) — a webhook
        #    push finalizes locally only, so its remote job is cleaned up
        #    here instead, off any request path.
        done_with_job = self.search(
            [("state", "=", "done"), ("job_id", "!=", False)], limit=50)
        if done_with_job:
            # Each purge is an outbound HTTP DELETE; re-declare the work so
            # running out of budget mid-sweep asks for an immediate re-run
            # rather than parking the rest for a full interval.
            Cron._commit_progress(remaining=len(done_with_job))
            for batch in done_with_job:
                batch._delete_remote_job()
                batch.job_id = False
                if Cron._commit_progress(processed=1) <= 0:
                    return
        elif budget_left <= 0:
            return

        # Nothing left to do — report a clean finish so the next run is the
        # scheduled one rather than an immediate re-trigger.
        Cron._commit_progress(remaining=0)

    @api.model
    def _submit_next_queued(self):
        """Submit the oldest queued batch, but only if no job is in flight.

        This enforces the one-job-at-a-time rule: a new list is never sent
        until the previous cycle has been fully imported (state left 'running').
        Returns the submitted batch, or an empty recordset.
        """
        # Serialize the search+submit decision across cron workers. The lock is
        # transaction-scoped and try-only, so a second worker exits immediately
        # instead of submitting another remote job from the same queue.
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            ("era_email_verification.submit",),
        )
        if not self.env.cr.fetchone()[0]:
            return self.browse()
        if self.search_count([("state", "=", "running")]):
            return self.browse()
        nxt = self.search(
            [("state", "=", "queued"), ("job_id", "=", False)], order="id", limit=1)
        if nxt:
            try:
                nxt._submit()
            except Exception as exc:  # noqa: BLE001
                nxt._mark_failed(exc)
        return nxt

    @api.model
    def _cron_enqueue_unchecked(self, limit=5000):
        """Queue never-checked / stale mailing-eligible contacts (opt-in).

        Guarded against pile-up on two fronts:

        * while an earlier recheck batch is still queued or running — those
          contacts still read as "unchecked" (their status only updates once
          results land), so without this every run would enqueue a brand-new
          duplicate batch for the very same addresses;
        * for a back-off window after a recheck batch failed — otherwise a
          verifier that is down means one identical failed batch (and its
          items) manufactured on every single run, indefinitely.
        """
        if self.search_count([
                ("source", "=", "recheck"), ("state", "in", ("queued", "running"))]):
            return self.browse()
        backoff = fields.Datetime.subtract(
            fields.Datetime.now(), minutes=self._recheck_backoff_minutes())
        if self.search_count([
                ("source", "=", "recheck"), ("state", "=", "failed"),
                ("write_date", ">", backoff)]):
            return self.browse()
        Partner = self.env["res.partner"]
        Contact = self.env["mailing.contact"]
        partners = Partner._ev_find_unchecked(limit=limit) | Partner._ev_find_stale(limit=limit)
        contacts = Contact._ev_find_unchecked(limit=limit) | Contact._ev_find_stale(limit=limit)
        if not partners and not contacts:
            return self.browse()
        targets = self._prepare_targets(partners, contacts)
        # No trigger: this already runs inside the scheduled action.
        return self._enqueue_targets(targets, source="recheck", trigger=False)
