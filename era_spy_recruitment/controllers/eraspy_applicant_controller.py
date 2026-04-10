# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

FAILURE_STATUSES = {"failed", "error", "not_found", "not found"}


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

        processed = 0
        unmatched = 0

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
            candidate_dict = candidate if isinstance(candidate, dict) else None
            status_lower = str(status or "").strip().lower()
            if status_lower in ("failed", "error", "not_found", "not found") and not error_message and not candidate:
                error_message = "No profile found"

            is_failure = status_lower in FAILURE_STATUSES or bool(error_message)
            is_duplicate = status_lower == "duplicate_query"
            queue_state = "done"
            queue_error = False

            # All DB work in a single separate cursor — fast, atomic, no conflicts.
            # Retry once on serialization failure (concurrent update from enrich action).
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    with request.env.registry.cursor() as cr:
                        env = api.Environment(cr, 1, {})  # uid=1 (superuser)
                        applicant_model = env["hr.applicant"]
                        applicant = self._match_applicant(applicant_model, request_id, identifier, candidate_dict, status_lower)

                        if not request_id and applicant and applicant.eraspy_last_request_id:
                            request_id = applicant.eraspy_last_request_id
                        if request_id and not item.get("requestId") and not item.get("request_id"):
                            item["requestId"] = str(request_id)

                        if not applicant:
                            unmatched += 1
                            _logger.warning(
                                "EraSpy applicant callback unmatched: request_id=%s status=%s identifier=%s",
                                request_id, status, identifier,
                            )
                            env["eraspy.applicant.callback.queue"].log_callback(
                                applicant=None, item=item, candidate=candidate_dict,
                                state="error", error="No applicant match found",
                            )
                            break  # don't retry unmatched

                        # duplicate_query: this identifier was already enriched before.
                        # Try to find the existing enrichment data from another applicant
                        # or a previous callback log and copy it over.
                        if is_duplicate:
                            copied = False
                            if identifier and not applicant.eraspy_more_data:
                                # Find another applicant that has data for this identifier
                                donor = applicant_model.search([
                                    ("eraspy_last_identifier", "ilike", identifier),
                                    ("eraspy_more_data", "!=", False),
                                    ("id", "!=", applicant.id),
                                ], limit=1)
                                if not donor:
                                    # Try from callback log
                                    log_record = env["eraspy.applicant.callback.queue"].search([
                                        ("identifier", "ilike", identifier),
                                        ("candidate_json", "!=", False),
                                        ("state", "=", "done"),
                                    ], limit=1, order="create_date desc")
                                    if log_record and log_record.candidate_json:
                                        try:
                                            prev_candidate = json.loads(log_record.candidate_json)
                                            if isinstance(prev_candidate, dict) and prev_candidate:
                                                write_vals = applicant._prepare_eraspy_write_vals(
                                                    prev_candidate, overwrite=False, skip_ai_match=True,
                                                )
                                                if write_vals:
                                                    applicant.write(write_vals)
                                                    copied = True
                                        except Exception:
                                            pass
                                if donor and not copied:
                                    # Copy enrichment fields from the donor applicant
                                    copy_fields = {}
                                    for fname in ("eraspy_more_data", "eraspy_profile_url",
                                                  "eraspy_rating", "eraspy_ai_match"):
                                        val = donor[fname]
                                        if val and not applicant[fname]:
                                            copy_fields[fname] = val
                                    if copy_fields:
                                        copy_fields["eraspy_last_status"] = "Enriched"
                                        copy_fields["eraspy_last_checked"] = fields.Datetime.now()
                                        applicant.write(copy_fields)
                                        copied = True

                            if not copied:
                                prev_status = applicant.eraspy_last_status or ""
                                if applicant._is_eraspy_queue_status(prev_status):
                                    applicant.write({
                                        "eraspy_last_status": applicant.eraspy_more_data and "Enriched" or "Ready",
                                        "eraspy_last_checked": fields.Datetime.now(),
                                    })

                            env["eraspy.applicant.callback.queue"].log_callback(
                                applicant=applicant, item=item, candidate=candidate_dict,
                                state="done", error=False,
                            )
                            processed += 1
                            _logger.info(
                                "EraSpy duplicate_query for applicant=%s copied=%s",
                                applicant.id, copied,
                            )
                            break

                        # Build write values
                        write_vals = {}
                        if candidate_dict and not is_failure:
                            write_vals = applicant._prepare_eraspy_write_vals(
                                candidate_dict, overwrite=False, skip_ai_match=True,
                            )
                        if status_lower in FAILURE_STATUSES and not error_message:
                            error_message = "No profile found"
                        if is_failure and not status:
                            status = "failed"
                        if status and is_failure:
                            write_vals.update({
                                "eraspy_last_status": f"{status}: {error_message}"[:255] if error_message else str(status)[:255],
                                "eraspy_last_checked": fields.Datetime.now(),
                            })

                        write_vals["eraspy_debug_payload"] = json.dumps(item, ensure_ascii=False)[:4000]

                        if write_vals:
                            applicant.write(write_vals)

                        # Log to callback queue
                        env["eraspy.applicant.callback.queue"].log_callback(
                            applicant=applicant, item=item, candidate=candidate_dict,
                            state="done", error=False,
                        )
                        processed += 1
                    break  # success — exit retry loop

                except Exception as exc:
                    if attempt < max_attempts - 1 and "serialize" in str(exc).lower():
                        import time as _time
                        _time.sleep(1)
                        _logger.warning("EraSpy callback retry after serialization: request_id=%s", request_id)
                        continue  # retry
                _logger.exception("EraSpy callback failed: request_id=%s identifier=%s", request_id, identifier)
                # Try to log the error
                try:
                    with request.env.registry.cursor() as cr:
                        env = api.Environment(cr, 1, {})
                        env["eraspy.applicant.callback.queue"].log_callback(
                            applicant=None, item=item, candidate=candidate_dict,
                            state="error", error=str(exc)[:2000],
                        )
                except Exception:
                    pass

        # AI match in background — completely decoupled, never blocks the response.
        # Runs after the HTTP response is sent (best-effort).
        _logger.info("EraSpy applicant callback done: processed=%s unmatched=%s", processed, unmatched)
        return request.make_response(
            json.dumps({"ok": True, "processed": processed, "unmatched": unmatched}),
            headers=[("Content-Type", "application/json")],
        )

    @staticmethod
    def _match_applicant(applicant_model, request_id, identifier, candidate_dict, status_lower):
        """Try to match the callback to an applicant using multiple strategies."""
        applicant = None

        if request_id:
            applicant = applicant_model.search(
                [("eraspy_last_request_id", "=", str(request_id))], limit=1,
            )
        if not applicant and identifier:
            applicant = applicant_model.search(
                [("eraspy_last_identifier", "ilike", identifier)], limit=1,
            )
        if not applicant and identifier:
            normalized = identifier.strip()
            digits = "".join(ch for ch in normalized if ch.isdigit())
            phone_probe = digits or normalized
            clauses = [("email_from", "ilike", normalized)]
            if "phone" in applicant_model._fields:
                clauses.append(("phone", "ilike", phone_probe))
            if "mobile" in applicant_model._fields:
                clauses.append(("mobile", "ilike", phone_probe))
            domain = clauses[0]
            for clause in clauses[1:]:
                domain = ["|", domain, clause]
            applicant = applicant_model.search(domain, limit=1)

        if not applicant and isinstance(candidate_dict, dict):
            emails = [e.get("value") for e in candidate_dict.get("emails", []) if isinstance(e, dict)] or []
            phones = [p.get("value") for p in candidate_dict.get("phones", []) if isinstance(p, dict)] or []
            clauses = []
            if emails:
                clauses.append(("email_from", "in", emails))
            if phones and "phone" in applicant_model._fields:
                clauses.append(("phone", "in", phones))
            if clauses:
                domain = clauses[0]
                for clause in clauses[1:]:
                    domain = ["|", domain, clause]
                applicant = applicant_model.search(domain, limit=1)

        # Last resort for failures: find queued applicant by identifier
        if not applicant and identifier and status_lower in FAILURE_STATUSES:
            applicant = applicant_model.search(
                [("eraspy_last_status", "ilike", "queued"), ("eraspy_last_identifier", "ilike", identifier)],
                limit=1,
            )
            if not applicant:
                applicant = applicant_model.search(
                    [("eraspy_last_status", "ilike", "queued"), ("email_from", "ilike", identifier)],
                    limit=1,
                )

        return applicant
