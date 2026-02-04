# -*- coding: utf-8 -*-
import logging
import re
from email.utils import parseaddr
from typing import List

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EraSpyEnrichWizard(models.TransientModel):
    _name = "eraspy.enrich.wizard"
    _description = "Enrich CRM leads via EraSpy"

    lead_ids = fields.Many2many("crm.lead", string="Leads", required=True)
    lookup_source = fields.Selection(
        [
            ("auto", "Automatic (email/phone/LinkedIn)"),
            ("email", "Email"),
            ("phone", "Phone"),
            ("linkedin", "LinkedIn URL"),
            ("query", "Free-form query"),
        ],
        default="auto",
        required=True,
        help="Pick how EraSpy should locate the contact. Automatic uses lead email, phone, or LinkedIn URL if available.",
    )
    query = fields.Char(string="Identifier or Query")
    without_contacts = fields.Boolean(
        string="Profile preview only (no credits)",
        default=lambda self: self._default_without_contacts(),
        help="When enabled, EraSpy returns public profile data without revealing contact details, saving credits.",
    )
    overwrite_existing = fields.Boolean(
        string="Overwrite existing lead fields",
        default=False,
        help="If disabled, only empty lead fields are filled.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        lead_ids = self.env.context.get("default_lead_ids") or self.env.context.get("active_ids") or []
        if not lead_ids and res.get("lead_ids"):
            lead_ids = self._extract_ids(res.get("lead_ids"))
        if lead_ids:
            leads = self.env["crm.lead"].browse(self._extract_ids(lead_ids))
            has_identifiers = any(self._lead_has_identifiers(lead) for lead in leads)
            if not has_identifiers:
                res.setdefault("lookup_source", "query")
        return res

    @api.onchange("lead_ids")
    def _onchange_lead_ids(self):
        if not self.lead_ids:
            return
        has_identifiers = any(self._lead_has_identifiers(lead) for lead in self.lead_ids)
        if not has_identifiers:
            self.lookup_source = "query"
        elif self.lookup_source == "query" and not self.query:
            self.lookup_source = "auto"

    @api.model
    def _default_without_contacts(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return ICP.get_param("era_spy.without_contacts_default") == "True"

    # ------------------------------------------------------------
    # Core actions
    # ------------------------------------------------------------
    def action_enrich(self):
        self.ensure_one()
        client = self.env["eraspy.client"]
        cfg = client._get_config()
        requested_ids = self._extract_ids(
            self.env.context.get("active_ids")
            or self.env.context.get("default_lead_ids")
            or []
        )
        if requested_ids:
            leads = self.lead_ids.filtered(lambda l: l.id in set(requested_ids))
        else:
            leads = self.lead_ids
        enriched = 0
        pending = 0
        pending_requests = []
        skipped = []
        request_debug = []
        response_debug = []
        _logger.info(
            "EraSpy enrich started: lookup_source=%s without_contacts=%s leads=%s",
            self.lookup_source,
            self.without_contacts,
            len(leads),
        )

        for lead in leads:
            identifiers = self._build_identifiers(lead)
            if not identifiers:
                skipped.append(lead.display_name)
                continue

            request_payload = {
                "endpoint": "/v1/candidate/search",
                "items": identifiers,
                "callbackUrl": cfg.get("callback_url"),
            }
            if self.without_contacts:
                request_payload["withoutContacts"] = True
            request_debug.append(request_payload)

            result = client.search_person(identifiers, without_contacts=self.without_contacts)
            response_debug.append(result)
            profile = self._extract_profile(result)
            if profile:
                lead._apply_eraspy_profile(profile, overwrite=self.overwrite_existing)
                enriched += 1
                continue

            request_id = result.get("requestId") or result.get("request_id")
            if request_id:
                lead.write(
                    {
                        "eraspy_last_status": _("Queued"),
                        "eraspy_last_checked": fields.Datetime.now(),
                        "eraspy_last_request_id": str(request_id),
                        "eraspy_last_identifier": identifiers[0] if identifiers else False,
                    }
                )
                pending += 1
                pending_requests.append(str(request_id))
                continue

            skipped.append(lead.display_name)

        message = _("%s lead(s) enriched via EraSpy.") % enriched
        if pending:
            message += _(" %s lead(s) queued; results will be applied on callback.") % pending
            if pending_requests:
                message += _(" Request IDs: %s") % ", ".join(pending_requests[:10])
        if self.without_contacts:
            message += _(" (Preview mode: contacts not requested)")
        if skipped:
            message += _(" Skipped: %s") % ", ".join(skipped)
        _logger.info(message)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("EraSpy"),
                "message": message,
                "type": "success" if enriched else "warning",
                "sticky": False,
                "next": {
                    "type": "ir.actions.client",
                    "tag": "eraspy_console_log",
                    "params": {"payload": request_debug, "responses": response_debug},
                },
            },
        }

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def _build_identifiers(self, lead) -> List[str]:
        identifiers = []
        emails = []
        phones = []

        def normalize_email(value):
            if not value:
                return None
            name, addr = parseaddr(value)
            addr = (addr or value).strip()
            if "@" not in addr:
                return None
            return addr.lower()

        def normalize_phone(value):
            if not value:
                return None
            raw = value.strip()
            if not raw:
                return None
            normalized = re.sub(r"[^\d+]", "", raw)
            if not normalized:
                return None
            # Normalize to a single leading '+' if present.
            if normalized.count("+") > 1:
                normalized = "+" + normalized.replace("+", "")
            return normalized
        if self.lookup_source in ("auto", "email") and lead.email_from:
            emails.append(lead.email_from)
        if self.lookup_source in ("auto", "phone") and lead.phone:
            phones.append(lead.phone)
        if self.lookup_source in ("auto", "phone") and getattr(lead, "mobile", False):
            phones.append(lead.mobile)
        if self.lookup_source in ("auto", "email") and lead.partner_id and lead.partner_id.email:
            emails.append(lead.partner_id.email)
        if self.lookup_source in ("auto", "phone") and lead.partner_id and lead.partner_id.phone:
            phones.append(lead.partner_id.phone)
        if self.lookup_source in ("auto", "phone") and lead.partner_id and getattr(lead.partner_id, "mobile", False):
            phones.append(lead.partner_id.mobile)
        linkedin = None
        if self.lookup_source in ("auto", "linkedin"):
            if getattr(lead, "linkedin_url", False):
                linkedin = lead.linkedin_url.strip()
            elif lead.website and "linkedin.com" in lead.website:
                linkedin = lead.website.strip()
        if self.lookup_source == "query" and self.query:
            identifiers.append(self.query.strip())

        normalized_emails = []
        for email in emails:
            normalized = normalize_email(email)
            if normalized:
                normalized_emails.append(normalized)

        normalized_phones = []
        for phone in phones:
            normalized = normalize_phone(phone)
            if normalized:
                normalized_phones.append(normalized)

        if self.lookup_source == "query" and self.query:
            identifiers = [self.query.strip()]
        else:
            # Priority/order: linkedin -> email(s) -> company -> phone(s)
            if linkedin:
                identifiers.append(linkedin)
            identifiers.extend(normalized_emails)
            if self.lookup_source == "auto" and lead.partner_name:
                identifiers.append(lead.partner_name.strip())
            identifiers.extend(normalized_phones)

        # Dedupe while preserving order.
        seen = set()
        deduped = []
        for item in identifiers:
            if item and item not in seen:
                seen.add(item)
                deduped.append(item)
        identifiers = deduped

        if not identifiers and self.lookup_source != "auto":
            raise UserError(self.env._("No identifier found for lead %s. Please provide a query or ensure the lead has data.") % lead.display_name)
        if identifiers:
            masked = []
            for identifier in identifiers:
                if "@" in identifier:
                    local, _, domain = identifier.partition("@")
                    masked.append(f"{local[:2]}***@{domain}")
                elif identifier.startswith("http"):
                    masked.append("linkedin:***")
                else:
                    masked.append(f"***{identifier[-4:]}")
            _logger.info("EraSpy identifiers for lead %s: %s", lead.id, ", ".join(masked))
        return identifiers

    def _lead_has_identifiers(self, lead) -> bool:
        identifiers = []
        emails = []
        phones = []
        if lead.email_from:
            emails.append(lead.email_from)
        if lead.phone:
            phones.append(lead.phone)
        if getattr(lead, "mobile", False):
            phones.append(lead.mobile)
        if lead.partner_id and lead.partner_id.email:
            emails.append(lead.partner_id.email)
        if lead.partner_id and lead.partner_id.phone:
            phones.append(lead.partner_id.phone)
        if lead.partner_id and getattr(lead.partner_id, "mobile", False):
            phones.append(lead.partner_id.mobile)
        if getattr(lead, "linkedin_url", False):
            identifiers.append(lead.linkedin_url.strip())
        elif lead.website and "linkedin.com" in lead.website:
            identifiers.append(lead.website.strip())

        def normalize_email(value):
            if not value:
                return None
            name, addr = parseaddr(value)
            addr = (addr or value).strip()
            if "@" not in addr:
                return None
            return addr.lower()

        def normalize_phone(value):
            if not value:
                return None
            raw = value.strip()
            if not raw:
                return None
            normalized = re.sub(r"[^\d+]", "", raw)
            if not normalized:
                return None
            if normalized.count("+") > 1:
                normalized = "+" + normalized.replace("+", "")
            return normalized

        for email in emails:
            normalized = normalize_email(email)
            if normalized:
                identifiers.append(normalized)
        for phone in phones:
            normalized = normalize_phone(phone)
            if normalized:
                identifiers.append(normalized)

        identifiers = list(dict.fromkeys([i for i in identifiers if i]))
        return bool(identifiers)

    @staticmethod
    def _extract_ids(value):
        if isinstance(value, int):
            return [value]
        if isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (list, tuple)):
                ids = []
                for cmd in value:
                    if not cmd:
                        continue
                    if cmd[0] == 6:
                        ids.extend(cmd[2] or [])
                    elif cmd[0] == 4:
                        ids.append(cmd[1])
                return [i for i in ids if isinstance(i, int)]
            return [i for i in value if isinstance(i, int)]
        return []


    @staticmethod
    def _extract_profile(result: dict):
        if not result:
            return {}
        def is_failed(payload):
            if not isinstance(payload, dict):
                return False
            status = str(payload.get("status") or "").lower()
            if status in ("failed", "error", "not_found", "not found"):
                return True
            if payload.get("error") or payload.get("message") or payload.get("errorMessage") or payload.get("error_message"):
                return True
            return False

        if isinstance(result.get("candidate"), dict):
            return result.get("candidate")
        if isinstance(result.get("profile"), dict):
            return result.get("profile")
        if is_failed(result):
            return {}

        profiles = result.get("profiles") or result.get("items") or result.get("data") or []
        if isinstance(profiles, dict):
            profiles = [profiles]
        if not profiles and result.get("profile"):
            profiles = [result.get("profile")]

        def unwrap(entry):
            if isinstance(entry, dict):
                if isinstance(entry.get("candidate"), dict):
                    return entry.get("candidate")
                if isinstance(entry.get("profile"), dict):
                    return entry.get("profile")
            return entry

        for entry in profiles:
            profile = unwrap(entry)
            if not isinstance(profile, dict):
                continue
            if is_failed(profile):
                continue
            status = str(profile.get("status") or "").lower()
            if profile.get("emails") or profile.get("phones") or status in ("found", "success"):
                return profile
        if profiles:
            profile = unwrap(profiles[0])
            return profile if isinstance(profile, dict) and not is_failed(profile) else {}
        return {}
