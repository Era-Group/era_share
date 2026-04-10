# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

FAILURE_STATUSES = {"failed", "error", "not_found", "not found"}


class EraSpyController(http.Controller):
    @http.route("/eraspy/callback", type="http", auth="public", csrf=False, methods=["POST"])
    def eraspy_callback(self, **kwargs):
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
                    _logger.warning("EraSpy callback JSON parse failed. Raw: %s", raw[:2000])
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

            # All DB work in a single separate cursor -- fast, atomic, no conflicts.
            # Retry once on serialization failure (concurrent update from enrich action).
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    with request.env.registry.cursor() as cr:
                        env = api.Environment(cr, 1, {})  # uid=1 (superuser)
                        lead_model = env["crm.lead"]
                        lead = self._match_lead(lead_model, request_id, identifier, candidate_dict, status_lower)

                        if not request_id and lead and lead.eraspy_last_request_id:
                            request_id = lead.eraspy_last_request_id
                        if request_id and not item.get("requestId") and not item.get("request_id"):
                            item["requestId"] = str(request_id)

                        if not lead:
                            unmatched += 1
                            _logger.warning(
                                "EraSpy callback unmatched: request_id=%s status=%s identifier=%s",
                                request_id, status, identifier,
                            )
                            env["eraspy.callback.queue"].log_callback(
                                lead=None, item=item, candidate=candidate_dict,
                                state="error", error="No lead match found",
                            )
                            break  # don't retry unmatched

                        # duplicate_query: this identifier was already enriched before.
                        # Try to find the existing enrichment data from another lead
                        # or a previous callback log and copy it over.
                        if is_duplicate:
                            copied = False
                            if identifier and not lead.eraspy_more_data:
                                # Find another lead that has data for this identifier
                                donor = lead_model.search([
                                    ("eraspy_last_identifier", "ilike", identifier),
                                    ("eraspy_more_data", "!=", False),
                                    ("id", "!=", lead.id),
                                ], limit=1)
                                if not donor:
                                    # Try from callback log
                                    log_record = env["eraspy.callback.queue"].search([
                                        ("identifier", "ilike", identifier),
                                        ("candidate_json", "!=", False),
                                        ("state", "=", "done"),
                                    ], limit=1, order="create_date desc")
                                    if log_record and log_record.candidate_json:
                                        try:
                                            prev_candidate = json.loads(log_record.candidate_json)
                                            if isinstance(prev_candidate, dict) and prev_candidate:
                                                write_vals = lead._prepare_eraspy_write_vals(
                                                    prev_candidate, overwrite=False, skip_ai_match=True,
                                                )
                                                if write_vals:
                                                    lead.write(write_vals)
                                                    copied = True
                                        except Exception:
                                            pass
                                if donor and not copied:
                                    # Copy enrichment fields from the donor lead
                                    copy_fields = {}
                                    for fname in ("eraspy_more_data", "eraspy_profile_url",
                                                  "eraspy_rating"):
                                        val = donor[fname]
                                        if val and not lead[fname]:
                                            copy_fields[fname] = val
                                    if copy_fields:
                                        copy_fields["eraspy_last_status"] = "Enriched"
                                        copy_fields["eraspy_last_checked"] = fields.Datetime.now()
                                        lead.write(copy_fields)
                                        copied = True

                            if not copied:
                                prev_status = lead.eraspy_last_status or ""
                                if lead._is_eraspy_queue_status(prev_status):
                                    lead.write({
                                        "eraspy_last_status": lead.eraspy_more_data and "Enriched" or "Ready",
                                        "eraspy_last_checked": fields.Datetime.now(),
                                    })

                            env["eraspy.callback.queue"].log_callback(
                                lead=lead, item=item, candidate=candidate_dict,
                                state="done", error=False,
                            )
                            processed += 1
                            _logger.info(
                                "EraSpy duplicate_query for lead=%s copied=%s",
                                lead.id, copied,
                            )
                            break

                        # Build write values
                        write_vals = {}
                        if candidate_dict and not is_failure:
                            write_vals = lead._prepare_eraspy_write_vals(
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
                            lead.write(write_vals)

                        # Log to callback queue
                        env["eraspy.callback.queue"].log_callback(
                            lead=lead, item=item, candidate=candidate_dict,
                            state="done", error=False,
                        )
                        processed += 1
                    break  # success -- exit retry loop

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
                            env["eraspy.callback.queue"].log_callback(
                                lead=None, item=item, candidate=candidate_dict,
                                state="error", error=str(exc)[:2000],
                            )
                    except Exception:
                        pass

        _logger.info("EraSpy callback done: processed=%s unmatched=%s", processed, unmatched)
        return request.make_response(
            json.dumps({"ok": True, "processed": processed, "unmatched": unmatched}),
            headers=[("Content-Type", "application/json")],
        )

    @staticmethod
    def _match_lead(lead_model, request_id, identifier, candidate_dict, status_lower):
        """Try to match the callback to a lead using multiple strategies."""
        lead = None

        if request_id:
            lead = lead_model.search(
                [("eraspy_last_request_id", "=", str(request_id))], limit=1,
            )
        if not lead and identifier:
            lead = lead_model.search(
                [("eraspy_last_identifier", "ilike", identifier)], limit=1,
            )
        if not lead and identifier:
            normalized = identifier.strip()
            digits = "".join(ch for ch in normalized if ch.isdigit())
            phone_probe = digits or normalized
            clauses = [("email_from", "ilike", normalized)]
            if "phone" in lead_model._fields:
                clauses.append(("phone", "ilike", phone_probe))
            if "mobile" in lead_model._fields:
                clauses.append(("mobile", "ilike", phone_probe))
            if "partner_id" in lead_model._fields and "phone" in lead_model.env["res.partner"]._fields:
                clauses.append(("partner_id.phone", "ilike", phone_probe))
            if "partner_id" in lead_model._fields and "mobile" in lead_model.env["res.partner"]._fields:
                clauses.append(("partner_id.mobile", "ilike", phone_probe))
            domain = clauses[0]
            for clause in clauses[1:]:
                domain = ["|", domain, clause]
            lead = lead_model.search(domain, limit=1)

        if not lead and isinstance(candidate_dict, dict):
            emails = [e.get("value") for e in candidate_dict.get("emails", []) if isinstance(e, dict)] or []
            phones = [p.get("value") for p in candidate_dict.get("phones", []) if isinstance(p, dict)] or []
            clauses = []
            if emails:
                clauses.append(("email_from", "in", emails))
            if phones and "phone" in lead_model._fields:
                clauses.append(("phone", "in", phones))
            if clauses:
                domain = clauses[0]
                for clause in clauses[1:]:
                    domain = ["|", domain, clause]
                lead = lead_model.search(domain, limit=1)

        # Last resort for failures: find queued lead by identifier
        if not lead and identifier and status_lower in FAILURE_STATUSES:
            lead = lead_model.search(
                [("eraspy_last_status", "ilike", "queued"), ("eraspy_last_identifier", "ilike", identifier)],
                limit=1,
            )
            if not lead:
                lead = lead_model.search(
                    [("eraspy_last_status", "ilike", "queued"), ("email_from", "ilike", identifier)],
                    limit=1,
                )

        return lead
