# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class EraSpyCallbackQueue(models.Model):
    _name = "eraspy.callback.queue"
    _description = "EraSpy Callback Queue"
    _order = "create_date desc"
    _sql_constraints = [
        ("eraspy_callback_queue_request_id_uniq", "unique(request_id)", "Request ID must be unique."),
    ]

    lead_id = fields.Many2one("crm.lead", required=False, ondelete="set null")
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

    def init(self):
        super().init()
        # Keep the newest record per request_id before applying the unique constraint.
        try:
            self._cr.execute(
                """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (PARTITION BY request_id ORDER BY id DESC) AS rn
                    FROM eraspy_callback_queue
                    WHERE request_id IS NOT NULL
                )
                DELETE FROM eraspy_callback_queue
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """
            )
        except Exception as exc:
            _logger.warning("EraSpy queue dedupe skipped: %s", exc)

    @api.model
    def enqueue(self, lead, item, candidate):
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
        candidate_dict = candidate if isinstance(candidate, dict) else None

        def is_failure(status_value, error_value):
            status_lower = str(status_value or "").lower()
            if status_lower in ("failed", "error", "not_found", "not found"):
                return True
            return bool(error_value)

        incoming_success = bool(candidate_dict) and not is_failure(status, error_message)

        if request_id:
            domain = [("request_id", "=", str(request_id))]
        else:
            domain = [("state", "=", "pending"), ("request_id", "=", False)]
            if lead:
                domain.append(("lead_id", "=", lead.id))
            else:
                domain.append(("lead_id", "=", False))
        existing = self.search(domain, limit=1)

        if existing:
            existing_success = False
            if existing.candidate_json:
                existing_success = True
            else:
                existing_status = str(existing.status or "").lower()
                if existing_status and existing_status not in ("failed", "error", "not_found", "not found"):
                    existing_success = True

            # Only overwrite failed/empty records when a success arrives.
            if not incoming_success or existing_success:
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
            if lead and not existing.lead_id:
                vals["lead_id"] = lead.id
            if lead and existing.lead_id and existing.lead_id.id != lead.id:
                _logger.warning(
                    "EraSpy queue request_id=%s already linked to lead_id=%s; ignoring new lead_id=%s",
                    request_id,
                    existing.lead_id.id,
                    lead.id,
                )
            existing.write(vals)
            return existing

        vals = {
            "lead_id": lead.id if lead else False,
            "request_id": str(request_id) if request_id else False,
            "identifier": identifier,
            "status": status,
            "error_message": error_message,
            "payload_json": json.dumps(item, ensure_ascii=False),
            "candidate_json": json.dumps(candidate_dict, ensure_ascii=False) if candidate_dict else None,
        }
        return self.create(vals)

    @api.model
    def cron_process_queue(self, limit=50):
        records = self.search([("state", "=", "pending")], limit=limit)
        for record in records:
            record._process_one()

    def _process_one(self):
        self.ensure_one()
        self.attempts += 1
        try:
            if not self.lead_id:
                self.state = "error"
                self.last_error = "No lead matched"
                return

            try:
                payload = json.loads(self.payload_json or "[]")
            except json.JSONDecodeError as exc:
                self.state = "error"
                self.last_error = f"Payload JSON decode failed: {exc}"
                return
            try:
                candidate_payload = json.loads(self.candidate_json or "{}") if self.candidate_json else None
            except json.JSONDecodeError as exc:
                self.state = "error"
                self.last_error = f"Candidate JSON decode failed: {exc}"
                candidate_payload = None

            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                payload = []

            applied_success = False
            failure_status = None
            write_vals = {}

            for idx, entry in enumerate(payload):
                if isinstance(entry, dict) and isinstance(entry.get("item"), dict):
                    item = entry.get("item") or {}
                    candidate = entry.get("candidate")
                elif isinstance(entry, dict) and "item" in entry and "candidate" in entry and "status" not in entry:
                    item = entry.get("item") or {}
                    candidate = entry.get("candidate")
                else:
                    item = entry if isinstance(entry, dict) else {}
                    candidate = None

                if not candidate and isinstance(candidate_payload, list) and idx < len(candidate_payload):
                    candidate = candidate_payload[idx]
                elif not candidate and isinstance(candidate_payload, dict):
                    candidate = candidate_payload

                if not candidate and isinstance(item, dict):
                    candidate = item.get("candidate") or item.get("profile")

                status = item.get("status") if isinstance(item, dict) else None
                error_message = (
                    item.get("error")
                    or item.get("message")
                    or item.get("errorMessage")
                    or item.get("error_message")
                    or item.get("reason")
                    or item.get("detail")
                    or item.get("details")
                ) if isinstance(item, dict) else None
                status_lower = str(status or "").lower()
                is_failure = status_lower in ("failed", "error", "not_found", "not found") or bool(error_message)
                if status and status.lower() in ("failed", "error", "not_found", "not found") and not error_message:
                    error_message = "No profile found"

                if isinstance(candidate, dict) and candidate and not is_failure and not applied_success:
                    write_vals = self.lead_id._prepare_eraspy_write_vals(candidate, overwrite=False)
                    applied_success = True

                if not applied_success and is_failure and not failure_status:
                    failure_status = (status, error_message)

            if not applied_success and failure_status:
                status, error_message = failure_status
                if status:
                    write_vals.update(
                        {
                            "eraspy_last_status": f"{status}: {error_message}"[:255]
                            if error_message
                            else status[:255],
                            "eraspy_last_checked": fields.Datetime.now(),
                        }
                    )
            if self.payload_json:
                write_vals["eraspy_debug_payload"] = self.payload_json[:4000]
            if write_vals:
                self.lead_id.write(write_vals)
            self.state = "done"
        except Exception as exc:
            _logger.exception("EraSpy queue processing failed for id=%s", self.id)
            self.last_error = str(exc)[:2000]
            if "could not serialize access due to concurrent update" in str(exc).lower():
                self.state = "pending"
            else:
                self.state = "error"

    def action_retry(self):
        for record in self:
            record.write(
                {
                    "state": "pending",
                    "last_error": False,
                    "attempts": 0,
                }
            )
