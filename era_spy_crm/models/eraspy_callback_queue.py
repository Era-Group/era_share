# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

FAILURE_STATUSES = {"failed", "error", "not_found", "not found"}
QUEUE_LIKE_STATUSES = {"queued", "queue", "pending", "in_queue", "in queue", "processing", "in_progress", "in progress"}


class EraSpyCallbackQueue(models.Model):
    _name = "eraspy.callback.queue"
    _description = "EraSpy Callback Queue"
    _order = "create_date desc"
    _request_id_unique = models.Constraint(
        "unique(request_id)",
        "Request ID must be unique.",
    )

    lead_id = fields.Many2one("crm.lead", required=False, ondelete="set null")
    request_id = fields.Char()
    identifier = fields.Char()
    status = fields.Char()
    error_message = fields.Char()
    payload_json = fields.Text()
    candidate_json = fields.Text()
    state = fields.Selection(
        [("done", "Done"), ("error", "Error")],
        default="done",
        required=True,
    )
    last_error = fields.Text()

    def init(self):
        super().init()
        # Keep the newest record per request_id before applying the unique constraint.
        try:
            self.env.cr.execute(
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

        # Force-disable the old processing cron (was created under noupdate=1
        # so XML changes cannot deactivate it).
        try:
            self.env.cr.execute(
                """
                UPDATE ir_cron SET active = FALSE
                WHERE id IN (
                    SELECT res_id FROM ir_model_data
                    WHERE module = 'era_spy_crm'
                    AND name = 'ir_cron_eraspy_process_queue'
                )
                AND active = TRUE
                """
            )
            if self.env.cr.rowcount:
                _logger.info("EraSpy: disabled legacy 'Process Callback Queue (CRM)' cron")
        except Exception as exc:
            _logger.warning("EraSpy: failed to disable legacy cron: %s", exc)

        # Drop legacy columns removed from the model to prevent ORM errors.
        for col in ("attempts",):
            try:
                self.env.cr.execute(
                    "ALTER TABLE eraspy_callback_queue DROP COLUMN IF EXISTS %s" % col
                )
            except Exception:
                pass

        # Migrate legacy "pending" state to "error" (selection no longer has "pending").
        try:
            self.env.cr.execute(
                "UPDATE eraspy_callback_queue SET state = 'error', "
                "last_error = 'Migrated from legacy pending state' "
                "WHERE state = 'pending'"
            )
        except Exception:
            pass

    @api.model
    def log_callback(self, lead, item, candidate, state="done", error=False):
        """Log a processed callback for audit purposes."""
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

        # Update existing record if one exists for this request_id
        if request_id:
            existing = self.search([("request_id", "=", str(request_id))], limit=1)
            if existing:
                existing.write({
                    "lead_id": lead.id if lead else False,
                    "identifier": identifier or existing.identifier,
                    "status": status,
                    "error_message": error_message,
                    "payload_json": json.dumps(item, ensure_ascii=False),
                    "candidate_json": json.dumps(candidate_dict, ensure_ascii=False) if candidate_dict else existing.candidate_json,
                    "state": state,
                    "last_error": error,
                })
                return existing

        vals = {
            "lead_id": lead.id if lead else False,
            "request_id": str(request_id) if request_id else False,
            "identifier": identifier,
            "status": status,
            "error_message": error_message,
            "payload_json": json.dumps(item, ensure_ascii=False),
            "candidate_json": json.dumps(candidate_dict, ensure_ascii=False) if candidate_dict else None,
            "state": state,
            "last_error": error,
        }
        return self.create(vals)

    @api.model
    def cron_cleanup_hanging_leads(self, timeout_minutes=15):
        """Mark leads stuck in 'Queued' status as failed."""
        cutoff = fields.Datetime.now() - timedelta(minutes=timeout_minutes)
        lead_model = self.env["crm.lead"].sudo()
        queued_leads = lead_model.search([
            ("eraspy_last_status", "ilike", "queued"),
            ("eraspy_last_checked", "<", cutoff),
        ])
        if queued_leads:
            _logger.warning(
                "EraSpy cleanup: marking %s stuck lead(s) as failed (queued before %s)",
                len(queued_leads), cutoff,
            )
            queued_leads.write({
                "eraspy_last_status": "failed: timed out",
                "eraspy_last_checked": fields.Datetime.now(),
            })

    def _match_lead(self):
        if "crm.lead" not in self.env:
            return False
        lead_model = self.env["crm.lead"].sudo()
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
            lead = lead_model.search([("eraspy_last_request_id", "=", str(request_id))], limit=1)
            if lead:
                return lead

        if identifier:
            lead = lead_model.search([("eraspy_last_identifier", "ilike", identifier)], limit=1)
            if lead:
                return lead

        if identifier:
            normalized = identifier.strip()
            digits = "".join(ch for ch in normalized if ch.isdigit())
            phone_probe = digits or normalized
            clauses = [("email_from", "ilike", normalized)]
            if "phone" in lead_model._fields:
                clauses.append(("phone", "ilike", phone_probe))
            if "mobile" in lead_model._fields:
                clauses.append(("mobile", "ilike", phone_probe))
            if "partner_id" in lead_model._fields and "phone" in self.env["res.partner"]._fields:
                clauses.append(("partner_id.phone", "ilike", phone_probe))
            if "partner_id" in lead_model._fields and "mobile" in self.env["res.partner"]._fields:
                clauses.append(("partner_id.mobile", "ilike", phone_probe))
            domain = build_or_domain(clauses)
            if domain:
                lead = lead_model.search(domain, limit=1)
                if lead:
                    return lead

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
            if phones and "phone" in lead_model._fields:
                clauses.append(("phone", "in", phones))
            if phones and "mobile" in lead_model._fields:
                clauses.append(("mobile", "in", phones))
            domain = build_or_domain(clauses)
            if domain:
                lead = lead_model.search(domain, limit=1)
                if lead:
                    return lead

        return False
