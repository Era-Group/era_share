# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class EraSpyApplicantController(http.Controller):
    @http.route("/eraspy/applicant/callback", type="http", auth="public", csrf=False, methods=["POST"])
    def eraspy_applicant_callback(self, **kwargs):
        payload = None
        try:
            payload = request.jsonrequest
        except Exception:
            payload = None
        if payload is None:
            raw = request.httprequest.data or b""
            if raw:
                try:
                    payload = json.loads(raw)
                except Exception:
                    _logger.warning("EraSpy applicant callback JSON parse failed. Raw: %s", raw[:2000])
                    payload = None
        if payload is None:
            payload = request.params or []

        root_request_id = None
        if isinstance(payload, dict):
            root_request_id = (
                payload.get("requestId")
                or payload.get("request_id")
                or payload.get("requestID")
                or payload.get("request")
            )
            for key in ("items", "data", "results"):
                items = payload.get(key)
                if isinstance(items, list):
                    payload = items
                    break
            if isinstance(payload, dict):
                payload = [payload]

        queue = request.env["eraspy.applicant.callback.queue"].sudo()
        applicant_model = request.env["hr.applicant"].sudo()
        queued = 0

        for item in payload or []:
            if not isinstance(item, dict):
                continue
            identifier = item.get("item")
            status = item.get("status")
            request_id = item.get("requestId") or item.get("request_id") or root_request_id
            error_message = (
                item.get("error")
                or item.get("message")
                or item.get("errorMessage")
                or item.get("error_message")
                or item.get("reason")
                or item.get("detail")
                or item.get("details")
            )
            candidate = item.get("candidate") or item.get("profile")
            if not candidate:
                candidate = None
            if status and status.lower() in ("failed", "not_found", "not found") and not error_message and not candidate:
                error_message = "No profile found"

            applicant = None
            if request_id:
                applicant = applicant_model.search(
                    [("eraspy_last_request_id", "=", str(request_id))],
                    limit=1,
                )
            if not applicant and identifier:
                applicant = applicant_model.search([("eraspy_last_identifier", "ilike", identifier)], limit=1)
            if not applicant and identifier:
                normalized = identifier.strip()
                digits = "".join(ch for ch in normalized if ch.isdigit())
                phone_probe = digits or normalized
                clauses = [("email_from", "ilike", normalized)]
                if "phone" in applicant_model._fields:
                    clauses.append(("phone", "ilike", phone_probe))
                if "mobile" in applicant_model._fields:
                    clauses.append(("mobile", "ilike", phone_probe))
                if "partner_id" in applicant_model._fields and "phone" in request.env["res.partner"]._fields:
                    clauses.append(("partner_id.phone", "ilike", phone_probe))
                if "partner_id" in applicant_model._fields and "mobile" in request.env["res.partner"]._fields:
                    clauses.append(("partner_id.mobile", "ilike", phone_probe))
                if clauses:
                    domain = clauses[0]
                    for clause in clauses[1:]:
                        domain = ["|", domain, clause]
                    applicant = applicant_model.search(domain, limit=1)

            if not applicant and isinstance(candidate, dict):
                emails = [e.get("value") for e in candidate.get("emails", []) if isinstance(e, dict)] or []
                phones = [p.get("value") for p in candidate.get("phones", []) if isinstance(p, dict)] or []
                clauses = []
                if emails:
                    clauses.append(("email_from", "in", emails))
                if phones and "phone" in applicant_model._fields:
                    clauses.append(("phone", "in", phones))
                if phones and "mobile" in applicant_model._fields:
                    clauses.append(("mobile", "in", phones))
                if clauses:
                    domain = clauses[0]
                    for clause in clauses[1:]:
                        domain = ["|", domain, clause]
                    applicant = applicant_model.search(domain, limit=1)

            if not request_id and applicant and applicant.eraspy_last_request_id:
                request_id = applicant.eraspy_last_request_id
            if request_id and not item.get("requestId") and not item.get("request_id"):
                item["requestId"] = str(request_id)

            _logger.info(
                "EraSpy applicant callback item: request_id=%s status=%s identifier=%s has_candidate=%s error=%s",
                request_id,
                status,
                identifier,
                bool(candidate),
                error_message,
            )

            queue.enqueue(applicant if applicant else None, item, candidate if isinstance(candidate, dict) else {})
            queued += 1

        _logger.info("EraSpy applicant callback queued: %s items", queued)
        return request.make_response(
            json.dumps({"ok": True, "queued": queued}),
            headers=[("Content-Type", "application/json")],
        )
