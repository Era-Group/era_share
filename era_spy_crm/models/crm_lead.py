# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = "crm.lead"

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

    eraspy_last_status = fields.Char(string="Era Enrich Last Status", readonly=True)
    eraspy_last_checked = fields.Datetime(string="Era Enrich Last Checked", readonly=True)
    eraspy_profile_url = fields.Char(string="Era Enrich Profile URL", readonly=True)
    linkedin_url = fields.Char(string="LinkedIn")
    eraspy_rating = fields.Char(string="Era Enrich Rating", readonly=True)
    eraspy_debug_payload = fields.Text(
        string="Era Enrich Debug Payload",
        readonly=True,
        groups="base.group_system",
    )
    eraspy_more_data = fields.Html(string="Era Enrich More Data", readonly=True)
    eraspy_last_request_id = fields.Char(string="Era Enrich Last Request ID", readonly=True)
    eraspy_last_identifier = fields.Char(string="Era Enrich Last Identifier", readonly=True)
    eraspy_enrichment_state = fields.Selection(
        [("queued", "Queued"), ("done", "Done"), ("failed", "Failed")],
        compute="_compute_eraspy_enrichment_state",
        store=True,
    )

    @api.depends("eraspy_last_status")
    def _compute_eraspy_enrichment_state(self):
        for lead in self:
            status = lead.eraspy_last_status
            if not status:
                lead.eraspy_enrichment_state = False
            elif lead._is_eraspy_queue_status(status):
                lead.eraspy_enrichment_state = "queued"
            elif "failed" in status.lower() or "error" in status.lower() or "timed out" in status.lower() or "not_found" in status.lower() or "not found" in status.lower():
                lead.eraspy_enrichment_state = "failed"
            else:
                lead.eraspy_enrichment_state = "done"

    @staticmethod
    def _is_eraspy_queue_status(status_value):
        status_lower = str(status_value or "").strip().lower()
        if not status_lower:
            return False
        if status_lower in ("queued", "queue", "pending", "in_queue", "in queue", "processing", "in_progress", "in progress"):
            return True
        return status_lower.startswith("queued:") or status_lower.startswith("queue:")

    def action_eraspy_enrich(self):
        lead_ids = self.ids or self.env.context.get("active_ids") or []
        if not lead_ids:
            raise UserError(_("Please select at least one lead."))
        if len(lead_ids) > 80:
            raise UserError(_("You can enrich at most 80 leads at a time. Please split your selection."))

        leads = self.browse(lead_ids)

        # Auto-reset leads stuck in Queued for over 2 minutes
        cutoff = fields.Datetime.now() - timedelta(minutes=2)
        for lead in leads:
            if (lead._is_eraspy_queue_status(lead.eraspy_last_status)
                    and lead.eraspy_last_checked
                    and lead.eraspy_last_checked < cutoff):
                lead.write({
                    "eraspy_last_status": "failed: timed out",
                    "eraspy_last_checked": fields.Datetime.now(),
                })

        ctx = dict(self.env.context or {})
        ctx.update(
            {
                "active_ids": lead_ids,
                "active_id": lead_ids[0] if lead_ids else False,
                "default_lead_ids": lead_ids,
                "default_lookup_source": "auto",
            }
        )
        wizard = self.env["eraspy.enrich.wizard"].with_context(ctx).create(
            {"lead_ids": [(6, 0, lead_ids)]}
        )
        return wizard.action_enrich()

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
        leads = self.search(
            [
                ("eraspy_auto_enrich_pending", "=", True),
                ("eraspy_auto_enrich_at", "<=", fields.Datetime.now()),
            ],
            limit=limit,
        )
        if not leads:
            return

        for lead in leads:
            try:
                cfg = client._get_config()
                wizard = self.env["eraspy.enrich.wizard"].with_context(
                    active_ids=[lead.id],
                    active_id=lead.id,
                    default_lead_ids=[lead.id],
                    default_lookup_source="auto",
                ).create(
                    {
                        "lead_ids": [(6, 0, [lead.id])],
                        "lookup_source": "auto",
                        "without_contacts": bool(cfg.get("without_contacts_default")),
                        "overwrite_existing": False,
                    }
                )
                wizard.action_enrich()
                lead.write({"eraspy_auto_enrich_pending": False})
            except UserError as exc:
                message = str(exc).lower()
                if "rate limit" in message or "429" in message:
                    lead.write({"eraspy_auto_enrich_at": fields.Datetime.now() + timedelta(minutes=1)})
                    return
                lead.write({"eraspy_auto_enrich_pending": False})
            except Exception:
                _logger.exception("EraSpy auto-enrich failed for lead %s", lead.id)
                lead.write({"eraspy_auto_enrich_pending": False})

    def _apply_eraspy_profile(self, profile: dict, overwrite=False):
        """Update lead with data returned by Era Enrich profile."""
        self.ensure_one()
        if not profile:
            raise UserError(_("Era Enrich did not return any profile data."))

        write_vals = self._prepare_eraspy_write_vals(profile, overwrite=overwrite, skip_ai_match=True)
        if write_vals:
            _logger.info("Applying EraSpy profile to lead %s: %s", self.id, write_vals)
            self.write(write_vals)
        else:
            _logger.info("EraSpy returned data but nothing new to write for lead %s", self.id)

    def _prepare_eraspy_write_vals(self, profile: dict, overwrite=False, skip_ai_match=False):
        """Build a single write dict for Era Enrich updates."""
        self.ensure_one()
        if not profile:
            return {}

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

        experiences = profile.get("experience") or []
        current_experience = None
        if isinstance(experiences, list):
            for exp in experiences:
                if isinstance(exp, dict) and exp.get("current"):
                    current_experience = exp
                    break
        if not current_experience and isinstance(experiences, list) and experiences:
            current_experience = experiences[0] if isinstance(experiences[0], dict) else None

        new_company = (
            profile.get("company")
            or (current_experience.get("company") if current_experience else False)
        )
        new_job = (
            profile.get("title")
            or profile.get("headLine")
            or (current_experience.get("position") if current_experience else False)
        )
        company_website = current_experience.get("website") if current_experience else False
        has_contact_data = bool(email_list or phone_list)

        write_vals = {}
        if new_email and (overwrite or not self.email_from):
            write_vals["email_from"] = new_email
        if new_phone and (overwrite or not self.phone):
            write_vals["phone"] = new_phone
        if new_name and (overwrite or not self.contact_name):
            write_vals["contact_name"] = new_name
        if new_company and (overwrite or not self.partner_name):
            write_vals["partner_name"] = new_company
        if new_job and (overwrite or not self.function):
            write_vals["function"] = new_job
        if company_website and (overwrite or not self.website):
            write_vals["website"] = company_website

        address_vals = self._extract_address_values(profile, overwrite=overwrite)
        if address_vals:
            write_vals.update(address_vals)

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

        profile_url = (
            profile.get("profileUrl")
            or profile.get("linkedinUrl")
            or profile.get("url")
        )
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
        update_keys = {"email_from", "phone", "contact_name", "partner_name", "function", "eraspy_rating", "eraspy_profile_url"}
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

        if not skip_ai_match and hasattr(self, "_build_eraspy_ai_match"):
            ai_match = self._build_eraspy_ai_match(profile)
            if ai_match:
                write_vals["eraspy_ai_match"] = ai_match

        write_vals["eraspy_last_status"] = str(status)[:255]
        write_vals["eraspy_last_checked"] = fields.Datetime.now()

        return write_vals

    def _extract_address_values(self, profile, overwrite=False):
        values = {}
        location_name = None
        locations = profile.get("locations")
        if isinstance(locations, list) and locations:
            loc = locations[0]
            location_name = loc.get("name") if isinstance(loc, dict) else loc
        elif isinstance(profile.get("location"), str):
            location_name = profile.get("location")

        street = None
        addresses = profile.get("addresses")
        if isinstance(addresses, list) and addresses:
            addr = addresses[0]
            if isinstance(addr, dict):
                street = addr.get("address") or addr.get("street") or addr.get("formatted")
            elif isinstance(addr, str):
                street = addr

        if street and (overwrite or not self.street):
            values["street"] = street

        if location_name:
            parts = [p.strip() for p in location_name.split(",") if p.strip()]
            city = parts[0] if parts else None
            state_name = parts[-2] if len(parts) >= 2 else None
            country_name = parts[-1] if len(parts) >= 1 else None

            if city and (overwrite or not self.city):
                values["city"] = city
            country = None
            if country_name:
                country = self.env["res.country"].search([("name", "ilike", country_name)], limit=1)
                if country and (overwrite or not self.country_id):
                    values["country_id"] = country.id
            if state_name:
                domain = [("name", "ilike", state_name)]
                if country:
                    domain.append(("country_id", "=", country.id))
                state = self.env["res.country.state"].search(domain, limit=1)
                if state and (overwrite or not self.state_id):
                    values["state_id"] = state.id
        return values

    def _build_eraspy_more_data(self, profile):
        if not isinstance(profile, dict):
            return ""
        # Use fallback formatter directly -- AI formatting has a timeout
        # that blocks the entire enrichment flow.
        return self._format_more_data_fallback(profile)

    @staticmethod
    def _trim_eraspy_profile(profile):
        if not isinstance(profile, dict):
            return {}
        trimmed = {}
        for key in (
            "uid", "fullName", "name", "gender", "headLine", "summary",
            "locations", "skills", "education", "experience", "social",
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

    def _try_copy_from_existing(self, lead, identifiers):
        """Try to copy enrichment data from another lead or callback log."""
        lead_model = self.env["crm.lead"].sudo()
        queue_model = self.env["eraspy.callback.queue"].sudo()

        for ident in identifiers:
            # 1. Find a donor lead with this identifier
            donor = lead_model.search([
                ("eraspy_last_identifier", "ilike", ident),
                ("eraspy_more_data", "!=", False),
                ("id", "!=", lead.id),
            ], limit=1)
            if donor:
                copy_fields = {}
                for fname in ("eraspy_more_data", "eraspy_profile_url",
                              "eraspy_rating", "eraspy_debug_payload"):
                    val = donor[fname]
                    if val:
                        copy_fields[fname] = val
                if copy_fields:
                    copy_fields.update({
                        "eraspy_last_status": "Enriched",
                        "eraspy_last_checked": fields.Datetime.now(),
                        "eraspy_last_identifier": ident,
                    })
                    lead.write(copy_fields)
                    _logger.info(
                        "Era Enrich copied enrichment from lead %s to %s (identifier=%s)",
                        donor.id, lead.id, ident,
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
                        write_vals = lead._prepare_eraspy_write_vals(
                            candidate, overwrite=False, skip_ai_match=True,
                        )
                        if write_vals:
                            write_vals["eraspy_last_identifier"] = ident
                            lead.write(write_vals)
                            _logger.info(
                                "Era Enrich copied enrichment from callback log %s to lead %s (identifier=%s)",
                                log_record.id, lead.id, ident,
                            )
                            return True
                except Exception:
                    pass

        return False
