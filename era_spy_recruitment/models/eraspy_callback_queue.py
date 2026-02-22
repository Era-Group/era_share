# -*- coding: utf-8 -*-
import json
import logging
import time

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

FAILURE_STATUSES = {"failed", "error", "not_found", "not found", "duplicate_query"}
QUEUE_LIKE_STATUSES = {"queued", "queue", "pending", "in_queue", "in queue", "processing", "in_progress", "in progress"}


class EraSpyApplicantCallbackQueue(models.Model):
    _name = "eraspy.applicant.callback.queue"
    _description = "EraSpy Applicant Callback Queue"
    _order = "create_date desc"
    _request_id_unique = models.Constraint(
        "unique(request_id)",
        "Request ID must be unique.",
    )

    applicant_id = fields.Many2one("hr.applicant", required=False, ondelete="set null")
    request_id = fields.Char()
    identifier = fields.Char()
    status = fields.Char()
    error_message = fields.Char()
    payload_json = fields.Text()
    candidate_json = fields.Text()
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Done"), ("error", "Error")],
        default="pending",
        required=True,
    )
    attempts = fields.Integer(default=0)
    last_error = fields.Text()
    processing_started_at = fields.Datetime(readonly=True)
    processing_finished_at = fields.Datetime(readonly=True)
    processing_seconds = fields.Float(readonly=True, digits=(16, 3))
    processing_total_seconds = fields.Float(readonly=True, digits=(16, 3))

    def init(self):
        super().init()
        # Keep the newest record per request_id before applying the unique constraint.
        try:
            self.env.cr.execute(
                """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (PARTITION BY request_id ORDER BY id DESC) AS rn
                    FROM eraspy_applicant_callback_queue
                    WHERE request_id IS NOT NULL
                )
                DELETE FROM eraspy_applicant_callback_queue
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """
            )
        except Exception as exc:
            _logger.warning("EraSpy applicant queue dedupe skipped: %s", exc)

    @api.model
    def enqueue(self, applicant, item, candidate):
        request_id = item.get("requestId") or item.get("request_id")
        identifier = item.get("item")
        status = item.get("status")
        error_message = (
            item.get("error")
            or item.get("message")
            or item.get("errorMessage")
            or item.get("error_message")
            or item.get("reason")
        )
        status_lower = str(status or "").strip().lower()
        if status_lower == "duplicate_query" and not error_message:
            error_message = "Duplicate query"
        candidate_dict = candidate if isinstance(candidate, dict) else None

        def is_failure(status_value, error_value):
            status_norm = str(status_value or "").strip().lower()
            if status_norm in FAILURE_STATUSES:
                return True
            return bool(error_value)

        incoming_success = bool(candidate_dict) and not is_failure(status, error_message)
        incoming_failure = is_failure(status, error_message)

        if request_id:
            domain = [("request_id", "=", str(request_id))]
        else:
            domain = [("state", "=", "pending"), ("request_id", "=", False)]
            if applicant:
                domain.append(("applicant_id", "=", applicant.id))
            else:
                domain.append(("applicant_id", "=", False))
        existing = self.search(domain, limit=1)

        if existing:
            if not existing.applicant_id and not applicant:
                _logger.warning(
                    "EraSpy applicant queue dropping orphan existing row id=%s request_id=%s identifier=%s",
                    existing.id,
                    request_id,
                    identifier,
                )
                existing.unlink()
                return False
            existing_has_candidate = bool(existing.candidate_json)
            existing_status = str(existing.status or "").strip().lower()
            existing_queue_like = existing_status in QUEUE_LIKE_STATUSES or existing_status.startswith("queued")
            existing_failure = is_failure(existing.status, existing.error_message)
            should_overwrite = False

            # A success callback should replace stale queue/failure/empty rows.
            if incoming_success and not existing_has_candidate:
                should_overwrite = True
            # A terminal failure callback (like duplicate_query) should replace queue-like rows.
            elif incoming_failure and (existing_queue_like or (not existing_has_candidate and not existing_status)):
                should_overwrite = True

            if not should_overwrite or (existing_has_candidate and not existing_failure):
                return existing

            vals = {
                "payload_json": json.dumps(item, ensure_ascii=False),
                "candidate_json": json.dumps(candidate_dict, ensure_ascii=False) if candidate_dict else None,
                "status": status,
                "error_message": error_message,
                "state": "pending",
                "last_error": False,
                "attempts": 0,
            }
            if request_id and not existing.request_id:
                vals["request_id"] = str(request_id)
            if identifier:
                vals["identifier"] = identifier
            if applicant and not existing.applicant_id:
                vals["applicant_id"] = applicant.id
            if applicant and existing.applicant_id and existing.applicant_id.id != applicant.id:
                _logger.warning(
                    "EraSpy applicant queue request_id=%s already linked to applicant_id=%s; ignoring new applicant_id=%s",
                    request_id,
                    existing.applicant_id.id,
                    applicant.id,
                )
            existing.write(vals)
            return existing

        if not applicant:
            _logger.warning(
                "EraSpy applicant queue skip create (no applicant): request_id=%s status=%s identifier=%s",
                request_id,
                status,
                identifier,
            )
            return False

        vals = {
            "applicant_id": applicant.id if applicant else False,
            "request_id": str(request_id) if request_id else False,
            "identifier": identifier,
            "status": status,
            "error_message": error_message,
            "payload_json": json.dumps(item, ensure_ascii=False),
            "candidate_json": json.dumps(candidate_dict, ensure_ascii=False) if candidate_dict else None,
        }
        return self.create(vals)

    @api.model
    def cron_process_queue(self, limit=10):
        # Keep callback queue clean from legacy orphan rows.
        orphan_records = self.search([("applicant_id", "=", False)])
        if orphan_records:
            _logger.warning("EraSpy applicant queue cleanup removed orphan rows: %s", len(orphan_records))
            orphan_records.unlink()
        records = self.search([("state", "=", "pending")], limit=limit)
        for record in records:
            record._process_one()

    def _process_one(self):
        self.ensure_one()
        started_at = fields.Datetime.now()
        started_ts = time.monotonic()
        self.processing_started_at = started_at
        self.attempts += 1
        try:
            if not self.applicant_id:
                self.state = "error"
                self.last_error = "Missing applicant_id"
                return

            try:
                item = json.loads(self.payload_json or "{}")
            except json.JSONDecodeError as exc:
                self.state = "error"
                self.last_error = f"Payload JSON decode failed: {exc}"
                return

            try:
                candidate = json.loads(self.candidate_json or "{}") if self.candidate_json else {}
            except json.JSONDecodeError as exc:
                self.state = "error"
                self.last_error = f"Candidate JSON decode failed: {exc}"
                candidate = {}

            status = item.get("status")
            error_message = (
                item.get("error")
                or item.get("message")
                or item.get("errorMessage")
                or item.get("error_message")
                or item.get("reason")
                or item.get("detail")
                or item.get("details")
            )
            status_lower = str(status or "").lower()
            if status_lower == "duplicate_query":
                status = "failed"
                status_lower = "failed"
                if not error_message:
                    error_message = "Duplicate query"
            is_failure = status_lower in FAILURE_STATUSES or bool(error_message)
            write_vals = {}
            if isinstance(candidate, dict) and candidate and not is_failure:
                write_vals = self.applicant_id._prepare_eraspy_write_vals(candidate, overwrite=False)
            if status and status.lower() in FAILURE_STATUSES and not error_message:
                error_message = "No profile found"
            if is_failure and not status:
                status = "failed"
            if status and is_failure:
                write_vals.update(
                    {
                        "eraspy_last_status": f"{status}: {error_message}"[:255] if error_message else status[:255],
                        "eraspy_last_checked": fields.Datetime.now(),
                    }
                )

            if self.payload_json:
                write_vals["eraspy_debug_payload"] = self.payload_json[:4000]

            if write_vals:
                self.applicant_id.write(write_vals)
            self.state = "done"
        except Exception as exc:
            _logger.exception("EraSpy applicant queue processing failed for id=%s", self.id)
            self.last_error = str(exc)[:2000]
            if "could not serialize access due to concurrent update" in str(exc).lower():
                self.state = "pending"
            else:
                self.state = "error"
        finally:
            elapsed = round(max(0.0, time.monotonic() - started_ts), 3)
            self.processing_finished_at = fields.Datetime.now()
            self.processing_seconds = elapsed
            self.processing_total_seconds = round((self.processing_total_seconds or 0.0) + elapsed, 3)

    def action_retry(self):
        for record in self:
            record.write(
                {
                    "state": "pending",
                    "last_error": False,
                    "attempts": 0,
                    "processing_started_at": False,
                    "processing_finished_at": False,
                    "processing_seconds": 0.0,
                    "processing_total_seconds": 0.0,
                }
            )

    def _match_applicant(self):
        if "hr.applicant" not in self.env:
            return False
        applicant_model = self.env["hr.applicant"].sudo()
        identifier = (self.identifier or "").strip()

        try:
            item = json.loads(self.payload_json or "{}")
        except json.JSONDecodeError:
            item = {}
        request_id = item.get("requestId") or item.get("request_id") or self.request_id

        def build_or_domain(clauses):
            if not clauses:
                return []
            domain = clauses[0]
            for clause in clauses[1:]:
                domain = ["|", domain, clause]
            return domain

        if request_id:
            applicant = applicant_model.search([("eraspy_last_request_id", "=", str(request_id))], limit=1)
            if applicant:
                return applicant

        if identifier:
            applicant = applicant_model.search([("eraspy_last_identifier", "ilike", identifier)], limit=1)
            if applicant:
                return applicant

        if identifier:
            normalized = identifier.strip()
            digits = "".join(ch for ch in normalized if ch.isdigit())
            phone_probe = digits or normalized
            clauses = [("email_from", "ilike", normalized)]
            if "phone" in applicant_model._fields:
                clauses.append(("phone", "ilike", phone_probe))
            if "mobile" in applicant_model._fields:
                clauses.append(("mobile", "ilike", phone_probe))
            if "partner_id" in applicant_model._fields and "phone" in self.env["res.partner"]._fields:
                clauses.append(("partner_id.phone", "ilike", phone_probe))
            if "partner_id" in applicant_model._fields and "mobile" in self.env["res.partner"]._fields:
                clauses.append(("partner_id.mobile", "ilike", phone_probe))
            domain = build_or_domain(clauses)
            if domain:
                applicant = applicant_model.search(domain, limit=1)
                if applicant:
                    return applicant

        try:
            candidate = json.loads(self.candidate_json or "{}") if self.candidate_json else {}
        except json.JSONDecodeError:
            candidate = {}
        if isinstance(candidate, dict):
            emails = [e.get("value") for e in candidate.get("emails", []) if isinstance(e, dict)] or []
            phones = [p.get("value") for p in candidate.get("phones", []) if isinstance(p, dict)] or []
            clauses = []
            if emails:
                clauses.append(("email_from", "in", emails))
            if phones and "phone" in applicant_model._fields:
                clauses.append(("phone", "in", phones))
            if phones and "mobile" in applicant_model._fields:
                clauses.append(("mobile", "in", phones))
            domain = build_or_domain(clauses)
            if domain:
                applicant = applicant_model.search(domain, limit=1)
                if applicant:
                    return applicant

        return False
