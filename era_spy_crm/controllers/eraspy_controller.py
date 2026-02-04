# -*- coding: utf-8 -*-
import json
import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


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

        lead_model = request.env["crm.lead"].sudo()
        updated = 0

        queue = request.env["eraspy.callback.queue"].sudo()
        lead_logs = {}
        unmatched = []

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

            debug_payload = json.dumps(item, ensure_ascii=True)
            if isinstance(candidate, dict):
                keys = sorted(list(candidate.keys()))
                emails = candidate.get("emails") or []
                phones = candidate.get("phones") or []
                contacts = candidate.get("contacts") or []
                _logger.info(
                    "EraSpy candidate keys=%s emails=%s phones=%s contacts=%s",
                    keys[:30],
                    len(emails) if isinstance(emails, list) else 1,
                    len(phones) if isinstance(phones, list) else 1,
                    len(contacts) if isinstance(contacts, list) else 1,
                )

            leads = lead_model.browse()
            if request_id:
                leads = lead_model.search([("eraspy_last_request_id", "=", str(request_id))], limit=50)
            if not leads and identifier:
                leads = lead_model.search([("eraspy_last_identifier", "ilike", identifier)], limit=50)
            if not leads and identifier:
                # Normalize identifier for matching; use digits for phone-like values.
                normalized = identifier.strip()
                digits = "".join(ch for ch in normalized if ch.isdigit())
                phone_probe = digits or normalized
                domain = [
                    "|",
                    "|",
                    "|",
                    ("email_from", "ilike", normalized),
                    ("phone", "ilike", phone_probe),
                    ("partner_id.phone", "ilike", phone_probe),
                    ("website", "ilike", normalized),
                ]
                if "mobile" in lead_model._fields:
                    domain = [
                        "|",
                        ("mobile", "ilike", phone_probe),
                        domain,
                    ]
                if "partner_id" in lead_model._fields and "mobile" in self.env["res.partner"]._fields:
                    domain = [
                        "|",
                        ("partner_id.mobile", "ilike", phone_probe),
                        domain,
                    ]
                leads = lead_model.search(domain, limit=50)

            if not leads and identifier:
                normalized = identifier.replace(" ", "").replace("-", "")
                digits = "".join(ch for ch in normalized if ch.isdigit())
                if digits:
                    leads = lead_model.search([("eraspy_last_identifier", "ilike", digits)], limit=50)
                    if leads:
                        pass
                domain = [
                    "|",
                    ("phone", "ilike", normalized),
                    ("partner_id.phone", "ilike", normalized),
                ]
                if "mobile" in lead_model._fields:
                    domain = [
                        "|",
                        ("mobile", "ilike", normalized),
                        domain,
                    ]
                if "partner_id" in lead_model._fields and "mobile" in self.env["res.partner"]._fields:
                    domain = [
                        "|",
                        ("partner_id.mobile", "ilike", normalized),
                        domain,
                    ]
                leads = lead_model.search(domain, limit=50)
                if not leads:
                    digits = "".join(ch for ch in normalized if ch.isdigit())
                    if digits:
                        domain = [
                            "|",
                            ("phone", "ilike", digits),
                            ("partner_id.phone", "ilike", digits),
                        ]
                        if "mobile" in lead_model._fields:
                            domain = [
                                "|",
                                ("mobile", "ilike", digits),
                                domain,
                            ]
                        if "partner_id" in lead_model._fields and "mobile" in self.env["res.partner"]._fields:
                            domain = [
                                "|",
                                ("partner_id.mobile", "ilike", digits),
                                domain,
                            ]
                        leads = lead_model.search(domain, limit=50)

            if not leads and isinstance(candidate, dict):
                emails = [e.get("value") for e in candidate.get("emails", []) if isinstance(e, dict)] or []
                phones = [p.get("value") for p in candidate.get("phones", []) if isinstance(p, dict)] or []
                if emails or phones:
                    domain = []
                    if emails:
                        domain = ["|", ("email_from", "in", emails), ("phone", "in", phones or ["__none__"])]
                    else:
                        domain = [("phone", "in", phones)]
                    leads = lead_model.search(domain, limit=50)

            if not request_id and leads and leads[0].eraspy_last_request_id:
                request_id = leads[0].eraspy_last_request_id
            if request_id and not item.get("requestId") and not item.get("request_id"):
                item["requestId"] = str(request_id)

            if not leads:
                unmatched.append(
                    {
                        "identifier": identifier,
                        "status": status,
                        "request_id": request_id,
                        "error": error_message,
                    }
                )
                queue.enqueue(None, item, candidate if isinstance(candidate, dict) else {})
                continue

            if isinstance(candidate, dict):
                candidate = dict(candidate)
                if status:
                    candidate.setdefault("status", status)
                if error_message:
                    candidate.setdefault("status_detail", error_message)
            for lead in leads:
                queue.enqueue(lead, item, candidate if isinstance(candidate, dict) else {})
                updated += 1
                entry = lead_logs.setdefault(
                    lead.id,
                    {
                        "name": lead.display_name,
                        "items": [],
                    },
                )
                entry["items"].append(
                    {
                        "identifier": identifier,
                        "status": status,
                        "request_id": request_id,
                        "has_candidate": bool(candidate),
                        "error": error_message,
                    }
                )

        for lead_id, info in lead_logs.items():
            items = info["items"]
            summary = "; ".join(
                "[%s|%s|%s]" % (i.get("identifier"), i.get("status"), i.get("request_id")) for i in items
            )
            _logger.info(
                "EraSpy callback lead=%s name=%s items=%s",
                lead_id,
                info.get("name"),
                summary,
            )
        if unmatched:
            preview = "; ".join(
                "[%s|%s|%s]" % (i.get("identifier"), i.get("status"), i.get("request_id"))
                for i in unmatched[:10]
            )
            _logger.warning("EraSpy callback unmatched items=%s total=%s", preview, len(unmatched))
        _logger.info("EraSpy callback processed: %s leads updated", updated)
        return request.make_response(
            json.dumps({"ok": True, "updated": updated}),
            headers=[("Content-Type", "application/json")],
        )
