# -*- coding: utf-8 -*-
import json
import logging
import re
from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import urlsplit


from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    eraspy_auto_enrich_pending = fields.Boolean(readonly=True, default=False)
    eraspy_auto_enrich_at = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get("eraspy_disable_auto_enrich"):
            return records
        ICP = self.env["ir.config_parameter"].sudo()
        if ICP.get_param("era_spy.auto_enrich_enabled", default="True") != "True":
            return records
        scheduled_at = fields.Datetime.now() + timedelta(minutes=1)
        records.write(
            {
                "eraspy_auto_enrich_pending": True,
                "eraspy_auto_enrich_at": scheduled_at,
            }
        )
        return records

    eraspy_last_status = fields.Char(readonly=True)
    eraspy_last_checked = fields.Datetime(readonly=True)
    eraspy_profile_url = fields.Char(string="EraSpy Profile URL", readonly=True)
    eraspy_rating = fields.Char(readonly=True)
    eraspy_debug_payload = fields.Text(
        string="EraSpy Debug Payload",
        readonly=True,
    )
    eraspy_more_data = fields.Html(string="EraSpy More Data", readonly=True)
    eraspy_last_request_id = fields.Char(string="EraSpy Last Request ID", readonly=True)
    eraspy_last_identifier = fields.Char(string="EraSpy Last Identifier", readonly=True)
    eraspy_ai_match = fields.Html(string="AI Qualification Match", readonly=True, sanitize=True)
    eraspy_enrichment_state = fields.Selection(
        [("queued", "Queued"), ("done", "Done"), ("failed", "Failed")],
        compute="_compute_eraspy_enrichment_state",
        store=True,
    )

    @api.depends("eraspy_last_status")
    def _compute_eraspy_enrichment_state(self):
        for applicant in self:
            status = applicant.eraspy_last_status
            if not status:
                applicant.eraspy_enrichment_state = False
            elif applicant._is_eraspy_queue_status(status):
                applicant.eraspy_enrichment_state = "queued"
            elif "failed" in status.lower() or "error" in status.lower() or "timed out" in status.lower() or "not_found" in status.lower() or "not found" in status.lower():
                applicant.eraspy_enrichment_state = "failed"
            else:
                applicant.eraspy_enrichment_state = "done"

    @staticmethod
    def _is_eraspy_queue_status(status_value):
        status_lower = str(status_value or "").strip().lower()
        if not status_lower:
            return False
        if status_lower in ("queued", "queue", "pending", "in_queue", "in queue", "processing", "in_progress", "in progress"):
            return True
        return status_lower.startswith("queued:") or status_lower.startswith("queue:")

    def action_eraspy_enrich(self):
        applicant_ids = self.ids or self.env.context.get("active_ids") or []
        if not applicant_ids:
            raise UserError(_("Please select at least one applicant."))
        applicants = self.browse(applicant_ids)
        # Auto-reset applicants stuck in Queued for over 2 minutes
        cutoff = fields.Datetime.now() - timedelta(minutes=2)
        for applicant in applicants:
            if (applicant._is_eraspy_queue_status(applicant.eraspy_last_status)
                    and applicant.eraspy_last_checked
                    and applicant.eraspy_last_checked < cutoff):
                applicant.write({
                    "eraspy_last_status": "failed: timed out",
                    "eraspy_last_checked": fields.Datetime.now(),
                })
        # Re-check after auto-reset
        blocked = applicants.filtered(
            lambda applicant: applicant._is_eraspy_queue_status(applicant.eraspy_last_status)
        )
        if blocked:
            names = ", ".join(blocked.mapped("display_name")[:5])
            more = len(blocked) - 5
            suffix = _(" and %s more") % more if more > 0 else ""
            raise UserError(_("EraSpy enrichment is already queued for: %s%s") % (names, suffix))
        if len(applicant_ids) > 80:
            raise UserError(_("You can enrich at most 80 applicants at a time. Please split your selection."))
        client = self.env["eraspy.client"]
        client._raise_if_rate_limited()
        cfg = client._get_config()
        callback_url = self._get_applicant_callback_url(cfg)
        without_contacts = bool(cfg.get("without_contacts_default"))

        enriched = 0
        pending = 0
        pending_requests = []
        skipped = []
        request_debug = []
        response_debug = []

        _logger.info(
            "EraSpy applicant enrich started: without_contacts=%s applicants=%s",
            without_contacts,
            len(applicants),
        )

        for applicant in applicants:
            identifiers = applicant._get_eraspy_identifiers()
            if not identifiers:
                skipped.append(applicant.display_name)
                continue

            # Check if another applicant already has enrichment data for
            # any of these identifiers — copy locally instead of calling API.
            if self._try_copy_from_existing(applicant, identifiers):
                enriched += 1
                continue

            request_payload = {
                "endpoint": "/v1/candidate/search",
                "items": identifiers,
                "callbackUrl": callback_url,
            }
            if without_contacts:
                request_payload["withoutContacts"] = True
            request_debug.append(request_payload)

            result = client.search_person(
                identifiers,
                without_contacts=without_contacts,
                callback_url=callback_url,
            )
            response_debug.append(result)

            profile = applicant._extract_profile(result)
            request_id = result.get("requestId") or result.get("request_id")
            if profile:
                applicant._apply_eraspy_profile(profile, overwrite=False)
                applicant.write(
                    {
                        "eraspy_debug_payload": json.dumps(result, ensure_ascii=True)[:4000],
                        "eraspy_last_identifier": identifiers[0] if identifiers else False,
                        "eraspy_last_request_id": str(request_id) if request_id else False,
                    }
                )
                enriched += 1
                continue

            if request_id:
                applicant.write(
                    {
                        "eraspy_last_status": _("Queued"),
                        "eraspy_last_checked": fields.Datetime.now(),
                        "eraspy_last_request_id": str(request_id),
                        "eraspy_last_identifier": identifiers[0] if identifiers else False,
                        "eraspy_debug_payload": json.dumps(result, ensure_ascii=True)[:4000],
                    }
                )
                pending += 1
                pending_requests.append(str(request_id))
                continue

            skipped.append(applicant.display_name)

        message = _("%s applicant(s) enriched via EraSpy.") % enriched
        if pending:
            message += _(" %s applicant(s) queued; results will be applied on callback.") % pending
            if pending_requests:
                message += _(" Request IDs: %s") % ", ".join(pending_requests[:10])
        if without_contacts:
            message += _(" (Preview mode: contacts not requested)")
        if skipped:
            message += _(" Skipped: %s") % ", ".join(skipped)

        _logger.info(message)

        # Return False — no page reload. The JS widget polls and refreshes
        # the form data automatically when the callback updates the status.
        return False

    @api.model
    def _cron_eraspy_auto_enrich(self, limit=80):
        ICP = self.env["ir.config_parameter"].sudo()
        if ICP.get_param("era_spy.auto_enrich_enabled", default="True") != "True":
            return
        client = self.env["eraspy.client"]
        try:
            client._raise_if_rate_limited()
        except UserError:
            return
        applicants = self.search(
            [
                ("eraspy_auto_enrich_pending", "=", True),
                ("eraspy_auto_enrich_at", "<=", fields.Datetime.now()),
            ],
            limit=limit,
        )
        if not applicants:
            return

        for applicant in applicants:
            try:
                cfg = client._get_config()
                identifiers = applicant._get_eraspy_identifiers()
                callback_url = applicant._get_applicant_callback_url(cfg)
                result = client.search_person(
                    identifiers,
                    without_contacts=bool(cfg.get("without_contacts_default")),
                    callback_url=callback_url,
                )
                profile = applicant._extract_profile(result)
                request_id = result.get("requestId") or result.get("request_id")
                if profile:
                    applicant._apply_eraspy_profile(profile, overwrite=False)
                    applicant.write(
                        {
                            "eraspy_debug_payload": json.dumps(result, ensure_ascii=True)[:4000],
                            "eraspy_last_identifier": identifiers[0] if identifiers else False,
                            "eraspy_last_request_id": str(request_id) if request_id else False,
                        }
                    )
                elif request_id:
                    applicant.write(
                        {
                            "eraspy_last_status": _("Queued"),
                            "eraspy_last_checked": fields.Datetime.now(),
                            "eraspy_last_request_id": str(request_id),
                            "eraspy_last_identifier": identifiers[0] if identifiers else False,
                            "eraspy_debug_payload": json.dumps(result, ensure_ascii=True)[:4000],
                        }
                    )
                applicant.write({"eraspy_auto_enrich_pending": False})
            except UserError as exc:
                message = str(exc).lower()
                if "rate limit" in message or "429" in message:
                    applicant.write({"eraspy_auto_enrich_at": fields.Datetime.now() + timedelta(minutes=1)})
                    return
                applicant.write({"eraspy_auto_enrich_pending": False})
            except Exception:
                _logger.exception("EraSpy auto-enrich failed for applicant %s", applicant.id)
                applicant.write({"eraspy_auto_enrich_pending": False})

    def _get_eraspy_identifiers(self):
        self.ensure_one()
        linkedin = None
        emails = []
        phones = []
        if self.email_from:
            emails.append(self.email_from)
        partner = getattr(self, "partner_id", False)
        if partner and partner.email:
            emails.append(partner.email)
        if getattr(self, "phone", False):
            phones.append(self.phone)
        if getattr(self, "mobile", False):
            phones.append(self.mobile)
        if not linkedin:
            for field_name in ("linkedin_profile", "linkedin_url", "linkedin"):
                if hasattr(self, field_name):
                    value = getattr(self, field_name)
                    if value:
                        linkedin = str(value).strip()
                        break
        if not linkedin and getattr(self, "website", False) and "linkedin.com" in self.website:
            linkedin = self.website.strip()
        if not linkedin and partner:
            for field_name in ("linkedin_profile", "linkedin_url", "linkedin"):
                if hasattr(partner, field_name):
                    value = getattr(partner, field_name)
                    if value:
                        linkedin = str(value).strip()
                        break
        if not linkedin and partner and getattr(partner, "website", False) and "linkedin.com" in partner.website:
            linkedin = partner.website.strip()

        def normalize_email(value):
            if not value:
                return None
            _, addr = parseaddr(value)
            addr = (addr or value).strip()
            if "@" not in addr:
                return None
            return addr.lower()

        def normalize_phone(value):
            if not value:
                return None
            raw = str(value).strip()
            if not raw:
                return None
            normalized = re.sub(r"[^\d+]", "", raw)
            if not normalized:
                return None
            if normalized.count("+") > 1:
                normalized = "+" + normalized.replace("+", "")
            return normalized

        identifiers = []
        if linkedin:
            identifiers.append(linkedin)
        for phone in phones:
            normalized = normalize_phone(phone)
            if normalized:
                identifiers.append(normalized)
        for candidate in emails:
            normalized = normalize_email(candidate)
            if normalized:
                identifiers.append(normalized)

        # Dedupe while preserving order.
        seen = set()
        deduped = []
        for item in identifiers:
            if item and item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    def _get_applicant_callback_url(self, cfg):
        ICP = self.env["ir.config_parameter"].sudo()
        explicit = ICP.get_param("era_spy.applicant_callback_url")
        if explicit:
            return explicit.strip()

        base_url = ""
        cfg_callback = (cfg or {}).get("callback_url")
        if cfg_callback:
            if "/eraspy/applicant/callback" in cfg_callback:
                return cfg_callback
            try:
                parts = urlsplit(cfg_callback)
                if parts.scheme and parts.netloc:
                    base_url = f"{parts.scheme}://{parts.netloc}"
                else:
                    base_url = cfg_callback.split("/eraspy/")[0].rstrip("/")
            except Exception:
                base_url = ""

        if not base_url:
            base_url = ICP.get_param("web.base.url", default="").rstrip("/")

        if base_url:
            return f"{base_url}/eraspy/applicant/callback"
        return cfg_callback

    def _try_copy_from_existing(self, applicant, identifiers):
        """Try to copy enrichment data from another applicant or callback log."""
        applicant_model = self.env["hr.applicant"].sudo()
        queue_model = self.env["eraspy.applicant.callback.queue"].sudo()

        for ident in identifiers:
            # 1. Find a donor applicant with this identifier
            donor = applicant_model.search([
                ("eraspy_last_identifier", "ilike", ident),
                ("eraspy_more_data", "!=", False),
                ("id", "!=", applicant.id),
            ], limit=1)
            if donor:
                copy_fields = {}
                for fname in ("eraspy_more_data", "eraspy_profile_url",
                              "eraspy_rating", "eraspy_ai_match",
                              "eraspy_debug_payload"):
                    val = donor[fname]
                    if val:
                        copy_fields[fname] = val
                if copy_fields:
                    copy_fields.update({
                        "eraspy_last_status": "Enriched",
                        "eraspy_last_checked": fields.Datetime.now(),
                        "eraspy_last_identifier": ident,
                    })
                    applicant.write(copy_fields)
                    _logger.info(
                        "EraSpy copied enrichment from applicant %s to %s (identifier=%s)",
                        donor.id, applicant.id, ident,
                    )
                    return True

            # 2. Find a callback log with candidate data for this identifier (last 20 days)
            log_cutoff = fields.Datetime.now() - timedelta(days=20)
            log_record = queue_model.search([
                ("identifier", "ilike", ident),
                ("candidate_json", "!=", False),
                ("state", "=", "done"),
                ("create_date", ">=", log_cutoff),
            ], limit=1, order="create_date desc")
            if log_record and log_record.candidate_json:
                try:
                    candidate = json.loads(log_record.candidate_json)
                    if isinstance(candidate, dict) and candidate:
                        write_vals = applicant._prepare_eraspy_write_vals(
                            candidate, overwrite=False, skip_ai_match=True,
                        )
                        if write_vals:
                            write_vals["eraspy_last_identifier"] = ident
                            applicant.write(write_vals)
                            _logger.info(
                                "EraSpy copied enrichment from callback log %s to applicant %s (identifier=%s)",
                                log_record.id, applicant.id, ident,
                            )
                            return True
                except Exception:
                    pass

        return False

    def _apply_eraspy_profile(self, profile: dict, overwrite=False):
        self.ensure_one()
        if not profile:
            raise UserError(_("EraSpy did not return any profile data."))

        write_vals = self._prepare_eraspy_write_vals(profile, overwrite=overwrite, skip_ai_match=True)
        if write_vals:
            _logger.info("Applying EraSpy profile to applicant %s: %s", self.id, write_vals)
            self.write(write_vals)
        else:
            _logger.info("EraSpy returned data but nothing new to write for applicant %s", self.id)

    def _prepare_eraspy_write_vals(self, profile: dict, overwrite=False, skip_ai_match=False):
        self.ensure_one()
        if not profile:
            return {}
        _logger.info(
            "EraSpy prepare write vals: applicant=%s profile_keys=%s",
            self.id,
            list(profile.keys())[:20],
        )

        def pick_first(values, keys):
            if isinstance(keys, str):
                keys = [keys]
            for item in values or []:
                if isinstance(item, dict):
                    for key in keys:
                        if item.get(key):
                            return item[key]
                if isinstance(item, str):
                    return item
            return False

        def extract_contact_list(key):
            values = profile.get(key)
            if values:
                return values if isinstance(values, list) else [values]
            for container_key in (
                "contacts",
                "contact",
                "contact_info",
                "contactInfo",
                "contactInfos",
                "contact_details",
                "contactDetails",
            ):
                container = profile.get(container_key)
                if isinstance(container, dict) and container.get(key):
                    nested = container.get(key)
                    return nested if isinstance(nested, list) else [nested]
            singular_key = key[:-1] if key.endswith("s") else key
            for alt_key in (
                singular_key,
                f"{singular_key}_value",
                f"{singular_key}Value",
                f"primary_{singular_key}",
                f"primary{singular_key.capitalize()}",
                f"{singular_key}Address",
                f"{singular_key}Number",
                f"{singular_key}_number",
                "e164",
                "phoneNumber",
                "phone_number",
                "emailAddress",
                "email_address",
            ):
                value = profile.get(alt_key)
                if value:
                    return [value]
            return []

        email_list = extract_contact_list("emails")
        phone_list = extract_contact_list("phones")

        contacts = profile.get("contacts") or profile.get("contact")
        if isinstance(contacts, list):
            for item in contacts:
                if not isinstance(item, dict):
                    continue
                contact_type = (item.get("type") or item.get("contactType") or item.get("kind") or "").lower()
                value = (
                    item.get("value")
                    or item.get("contact")
                    or item.get("email")
                    or item.get("phone")
                    or item.get("number")
                )
                if not value:
                    continue
                if ("mail" in contact_type or "email" in contact_type) or ("@" in str(value)):
                    email_list.append(value)
                if "phone" in contact_type or "mobile" in contact_type or "tel" in contact_type:
                    phone_list.append(value)
                if not contact_type and isinstance(value, str) and value.isdigit():
                    phone_list.append(value)

        new_email = pick_first(email_list, ["value", "email", "address", "mail", "emailAddress", "email_address"])
        new_phone = pick_first(phone_list, ["value", "number", "phone", "mobile", "phoneNumber", "phone_number", "e164"])
        new_name = profile.get("name") or profile.get("full_name") or profile.get("fullName")

        write_vals = {}
        if new_email and "email_from" in self._fields and (overwrite or not self.email_from):
            write_vals["email_from"] = new_email
        if new_phone and "phone" in self._fields and (overwrite or not self.phone):
            write_vals["phone"] = new_phone
        if new_phone and "mobile" in self._fields and (overwrite or not self.mobile):
            write_vals["mobile"] = new_phone
        if new_name and "partner_name" in self._fields and (overwrite or not self.partner_name):
            write_vals["partner_name"] = new_name

        rating_value = profile.get("rating")
        if rating_value is None:
            rating_candidates = []
            for container in (profile.get("contacts"), profile.get("emails"), profile.get("phones")):
                if isinstance(container, list):
                    for item in container:
                        if not isinstance(item, dict):
                            continue
                        for key in ("rating", "score", "quality", "accuracy"):
                            if item.get(key) is not None:
                                rating_candidates.append(item.get(key))
            numeric = [r for r in rating_candidates if isinstance(r, (int, float))]
            if numeric:
                rating_value = max(numeric)
            elif rating_candidates:
                rating_value = ", ".join([str(r) for r in rating_candidates])
        if rating_value is not None:
            write_vals["eraspy_rating"] = str(rating_value)

        profile_url = profile.get("profileUrl") or profile.get("linkedinUrl") or profile.get("url")
        if not profile_url:
            social = profile.get("social")
            if isinstance(social, list):
                for item in social:
                    if isinstance(item, dict):
                        url = item.get("url") or item.get("link") or item.get("value")
                    else:
                        url = item
                    if isinstance(url, str) and "linkedin.com" in url:
                        profile_url = url
                        break
        if profile_url:
            write_vals["eraspy_profile_url"] = profile_url

        status = profile.get("status") or _("Enriched")
        status_detail = profile.get("status_detail") or profile.get("error") or profile.get("message")
        update_keys = {"email_from", "phone", "mobile", "partner_name", "eraspy_rating", "eraspy_profile_url"}
        has_contact_data = bool(email_list or phone_list)
        if not status_detail and isinstance(status, str) and status.lower() in ("success", "ok"):
            if not has_contact_data:
                status_detail = _("No contact data returned (check credits or preview mode)")
            elif not (set(write_vals.keys()) & update_keys):
                status_detail = _("No new data applied (already present)")
        if status_detail and isinstance(status, str):
            status_lower = status.lower()
            if status_lower in ("failed", "error", "not_found", "not found") and ":" not in status:
                status = f"{status}: {status_detail}"
            if status_lower in ("success", "ok") and ":" not in status:
                status = f"{status}: {status_detail}"

        more_data = self._build_eraspy_more_data(profile)
        if more_data:
            write_vals["eraspy_more_data"] = more_data

        if not skip_ai_match:
            ai_match = self._build_eraspy_ai_match(profile)
            if ai_match:
                write_vals["eraspy_ai_match"] = ai_match

        write_vals["eraspy_last_status"] = str(status)[:255]
        write_vals["eraspy_last_checked"] = fields.Datetime.now()
        return write_vals

    def _build_eraspy_more_data(self, profile):
        if not isinstance(profile, dict):
            return ""
        # Use fallback formatter directly — AI formatting via _call_ai_agent
        # has a 120s timeout that blocks the entire enrichment flow.
        return self._format_more_data_fallback(profile)

    def _format_more_data_with_ai(self, profile):
        trimmed = self._trim_eraspy_profile(profile)
        payload = json.dumps(trimmed, ensure_ascii=False)
        prompt = (
            "Format this candidate profile into a concise HTML summary with sections and bullets. "
            "Output only HTML. Use <div>, <strong>, and bullet-like structure. Avoid tables, scripts, or styles."
        )
        html = self.env["eraspy.client"]._call_ai_agent(prompt, payload)
        if not html:
            return ""
        if html.startswith("```"):
            html = html.replace("```html", "").replace("```", "").strip()
        if "<" not in html:
            return ""
        return html[:4000]

    def _build_eraspy_ai_match(self, profile):
        job_description = self._get_job_description()
        job_title = self.job_id.name if getattr(self, "job_id", False) else ""
        if not job_description:
            if not job_title:
                return ""
            job_description = f"Job title only (no detailed description): {job_title}"

        trimmed = self._trim_eraspy_profile(profile)
        _logger.info(
            "EraSpy AI match start: applicant=%s job_title=%s profile_keys=%s",
            self.id,
            job_title,
            list(trimmed.keys())[:20],
        )
        payload = "\n".join(
            [
                f"Job Title: {job_title or 'N/A'}",
                f"Job Description: {job_description}",
                "Candidate Profile:",
                json.dumps(trimmed, ensure_ascii=False),
            ]
        )
        prompt = """Compare the job description to the candidate profile and provide a short qualification match.
Requirements:
- Include an overall fit rating from 1–10.
- Identify key strengths and key gaps.
- Use an evidence-based, objective approach only (do not assume missing information).

Scoring Logic (Internal – do not explain):
- Evaluate the match using weighted criteria:
  • Skills Match (40%)
  • Relevant Experience (30%)
  • Education/Certifications (15%)
  • Responsibilities / Domain Fit (15%)
- If a criterion is not mentioned in the job description, exclude it and re-normalize weights.
- If employee data is missing for a required criterion, score it low (0–20) depending on criticality.
- Ensure calculated percentages are consistent with the applied weights.

Output Format (Strict):
- Output ONLY valid HTML.
- Use ONLY <div> and <strong>.
- Use bullet-like lines using symbols (e.g. • or -), NOT lists or tables.
- Do NOT use tables, scripts, styles, JSON, or code blocks.

HTML Content Structure:
1. Arabic section FIRST, then English section.
2. Each language section must include:
   - Overall Fit Rating (1–10)
   - Job Match Percentage (0–100%)
   - Key Strengths
   - Key Gaps
   - Short Hiring Guidance (Strong Fit / Potential Fit / Not Recommended)
3. Keep the summary concise and readable.

Language Rules:
- Output in TWO languages:
  • Arabic (Modern Standard Arabic). add dir="rtl" to Arabic section.
  • English (add dir="ltr" to English section)
- Arabic content must appear before English.

Output ONLY the HTML. Do not include explanations, comments, or metadata.
"""
        text = self.env["eraspy.client"]._call_ai_agent(prompt, payload)
        if not text:
            _logger.warning("EraSpy AI match empty response: applicant=%s", self.id)
            return ""
        _logger.info("EraSpy AI match result: applicant=%s len=%s", self.id, len(text))
        if text.startswith("```"):
            text = text.replace("```", "").strip()
        return text

    def _get_job_description(self):
        job = getattr(self, "job_id", False)
        if not job:
            return ""
        parts = []
        for field_name in ("description", "requirements", "job_description", "description_job"):
            if field_name in job._fields:
                value = job[field_name]
                if value:
                    parts.append(value)
        return "\n\n".join([p.strip() for p in parts if isinstance(p, str) and p.strip()])

    @staticmethod
    def _trim_eraspy_profile(profile):
        if not isinstance(profile, dict):
            return {}
        trimmed = {}
        for key in (
            "uid",
            "fullName",
            "name",
            "gender",
            "headLine",
            "summary",
            "locations",
            "skills",
            "education",
            "experience",
            "social",
        ):
            value = profile.get(key)
            if isinstance(value, list):
                trimmed[key] = value[:10]
            else:
                trimmed[key] = value
        return trimmed

    @staticmethod
    def _format_more_data_fallback(profile):
        rows = []

        def add_row(label, value):
            if value is None:
                return
            if isinstance(value, list):
                cleaned = [str(v).strip().replace("\n", " / ") for v in value if v]
                if cleaned:
                    rows.append((label, cleaned))
                return
            text = str(value).strip().replace("\n", " / ")
            if text:
                rows.append((label, text))

        add_row("UID", profile.get("uid"))
        add_row("Full Name", profile.get("fullName") or profile.get("full_name") or profile.get("name"))
        add_row("Gender", profile.get("gender"))
        add_row("Headline", profile.get("headLine") or profile.get("headline"))
        add_row("Summary", profile.get("summary"))

        locations = profile.get("locations")
        if isinstance(locations, list):
            add_row("Locations", [loc.get("name") if isinstance(loc, dict) else loc for loc in locations])

        skills = profile.get("skills")
        if isinstance(skills, list):
            add_row("Skills", skills)

        education = profile.get("education")
        if isinstance(education, list):
            edu_lines = []
            for edu in education:
                if not isinstance(edu, dict):
                    continue
                title = " - ".join([v for v in [edu.get("university"), edu.get("faculty")] if v])
                degree = edu.get("degree")
                if isinstance(degree, list):
                    degree = ", ".join([str(d) for d in degree if d])
                years = " - ".join([str(y) for y in [edu.get("startedYear"), edu.get("endedYear")] if y])
                line = " | ".join([part for part in [title, degree, years] if part])
                if line:
                    edu_lines.append(line)
            add_row("Education", edu_lines)

        experience = profile.get("experience")
        if isinstance(experience, list):
            exp_lines = []
            for exp in experience:
                if not isinstance(exp, dict):
                    continue
                title = exp.get("position") or exp.get("title")
                company = exp.get("company")
                industry = exp.get("industry")
                website = exp.get("website")
                years = " - ".join([str(y) for y in [exp.get("started"), exp.get("ended")] if y])
                line = " | ".join([part for part in [title, company, industry, website, years] if part])
                if line:
                    exp_lines.append(line)
            add_row("Experience", exp_lines)

        social = profile.get("social")
        if isinstance(social, list):
            social_links = []
            for item in social:
                if isinstance(item, dict):
                    social_links.append(item.get("url") or item.get("link") or item.get("value"))
                elif isinstance(item, str):
                    social_links.append(item)
            add_row("Social", [s for s in social_links if s])

        photo = profile.get("photo")
        if isinstance(photo, dict):
            add_row("Photo", photo.get("url"))

        if not rows:
            return ""

        def esc(text):
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<div class=\"o_eraspy_more_data\" style=\"line-height:1.5;\">"]
        for key, value in rows:
            safe_key = esc(key)
            if isinstance(value, list):
                lines.append(f"<div><strong>{safe_key}:</strong></div>")
                for item in value:
                    safe_item = esc(str(item))
                    lines.append(f"<div style=\"margin-left: 1.2em;\">&bull; {safe_item}</div>")
            else:
                safe_value = esc(str(value))
                lines.append(f"<div><strong>{safe_key}:</strong> {safe_value}</div>")
        lines.append("</div>")
        result = "\n".join(lines)
        return result[:4000]

    @staticmethod
    def _extract_profile(result: dict):
        if not result:
            return {}
        profiles = result.get("profiles") or result.get("items") or result.get("data") or []
        if isinstance(profiles, dict):
            profiles = [profiles]
        if not profiles and result.get("profile"):
            profiles = [result.get("profile")]

        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            if profile.get("emails") or profile.get("phones") or profile.get("status") in ("found", "success"):
                return profile
        return profiles[0] if profiles else {}
