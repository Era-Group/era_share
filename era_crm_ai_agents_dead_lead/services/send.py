# -*- coding: utf-8 -*-
"""Dead-Lead WhatsApp send via the WAHA connector (task 3.6).

    send_via_waha(lead, text) -> bool

Called from ``crm.ai.dead.lead.agent._ai_on_approved`` AFTER a human approves a
drafted comeback (3.5). It transmits the approved text to the lead's WhatsApp,
then updates the lead (chatter note + a "last sent" stamp) and writes one masked
audit entry (Rule 20).

============================ EGRESS (documented here; not yet in the project
Egress Registry) ============================
This is a NON-LLM outbound egress: the lead's phone number AND the approved
message are relayed by the WAHA connector over the WhatsApp Web protocol to
WhatsApp/Meta — data leaves our infrastructure to a non-Saudi processor. It is
distinct from Module 2's WAHA *presence check* (which sent only a number). It
fires only after: a qualifying trigger (3.3) + a drafted message (3.4) + a
PASSED compliance guard (consent + send-window + norms) AND human approval
(3.5). The connector is SOFT-detected via the registry — never a hard depend;
if absent, the send fails closed (audited, returns False), never raises.

============================ SUDO ELEVATION (narrow, documented here) =========
The vendor model ``sadeem.waha.session`` is gated behind its own
``group_waha_whatsapp_user``, which a CRM salesperson/approver does not hold.
So the CONNECTOR interaction (find the ready session + ``send_message``) uses a
single-purpose ``.sudo()`` — vendor-infra access only, nothing else rides on it.
ALL agent-data writes (the lead stamp, the chatter note, the audit row) run as
the calling user — NO sudo (Rule 09). The audit's own create-only sudo is the
Base's already-approved elevation.

Rule 03 / vendor-logging caveat: the vendor's ``_make_api_request`` logs
``response.text`` on error (vendor code we do not modify). We therefore pass only
the bare number + our approved text, and OUR logs/audit store only a masked
handle (``wa:<sha256[:12]>``) + the exception CLASS name — never the raw number
or the raw vendor exception.
"""
import hashlib
import logging
import re

from odoo import _, fields

_logger = logging.getLogger(__name__)

WAHA_SESSION_MODEL = "sadeem.waha.session"
WAHA_READY_STATUS = "working"
CHANNEL = "whatsapp"


class WahaSender:
    """``agent`` is a ``crm.ai.dead.lead.agent`` recordset (may be the empty
    model recordset when called from the approval callback — we only use its
    ``env`` and the mixin's audit helper, never a stored config field)."""

    def __init__(self, agent):
        self.agent = agent
        self.env = agent.env

    # ------------------------------------------------------------------
    def send_via_waha(self, lead, text):
        """Send ``text`` to ``lead``'s WhatsApp; update the lead; audit. Returns
        True on success, False on any fail-closed path (never raises outward)."""
        if not text:
            self._audit_fail(lead, "empty content")
            return False

        session = self._ready_session()
        if not session:
            self._audit_fail(lead, "waha connector unavailable")
            return False

        partner = lead.partner_id
        phone = self._recipient_phone(lead)
        chat_id = self._chat_id(phone, partner)
        if not chat_id:
            self._audit_fail(lead, "no valid international phone")
            return False

        try:
            # NARROW SUDO: vendor connector call only (session is a sudo
            # recordset). We log only the exception CLASS — never str(exc),
            # which could echo the vendor's response.text (Rule 03).
            session.send_message(chat_id, text)
        except Exception as exc:  # noqa: BLE001 — a send failure must not raise out
            _logger.warning(
                "Dead-Lead: WhatsApp send failed for lead %s (%s).",
                lead.id, type(exc).__name__)
            self._audit_fail(lead, "send error: %s" % type(exc).__name__)
            return False

        self._after_send(lead, text, phone)
        return True

    # ------------------------------------------------------------------
    # Connector (narrow sudo — vendor infra only)
    # ------------------------------------------------------------------
    def _ready_session(self):
        if WAHA_SESSION_MODEL not in self.env:
            return None
        try:
            Session = self.env[WAHA_SESSION_MODEL].sudo()
            return Session.search(
                [("active", "=", True), ("status", "=", WAHA_READY_STATUS)],
                limit=1) or None
        except Exception:  # noqa: BLE001 — probing must never raise
            return None

    # ------------------------------------------------------------------
    # Recipient resolution (no mobile field in this CE — phone only)
    # ------------------------------------------------------------------
    def _recipient_phone(self, lead):
        partner = lead.partner_id
        return (partner.phone if partner else False) or lead.phone or ""

    def _chat_id(self, phone, partner):
        """Normalise to a WAHA chatId ``<digits>@c.us``. International by '+' or
        '00'; a national number (leading 0) is upgraded using the partner's
        country calling code (data-driven, never a hardcoded country). Anything
        that cannot be made international fails closed (None) — we never guess a
        recipient."""
        if not phone:
            return None
        p = re.sub(r"[\s\-()\.]", "", phone.strip())
        if p.startswith("+"):
            p = p[1:]
        elif p.startswith("00"):
            p = p[2:]
        elif p.startswith("0"):
            # National format — usable ONLY if we can resolve a country code;
            # otherwise fail closed (never guess a recipient by keeping the 0).
            code = partner.country_id.phone_code if partner else False
            if not code:
                return None
            p = str(code) + p[1:]
        digits = "".join(ch for ch in p if ch.isdigit())
        if len(digits) < 10:
            return None
        return "%s@c.us" % digits

    # ------------------------------------------------------------------
    # Post-send: lead update + audit (NON-sudo — runs as the approver)
    # ------------------------------------------------------------------
    def _after_send(self, lead, text, phone):
        # 1. Stamp (cooldown signal for the scan, 3.7).
        lead.write({"crm_ai_dead_lead_last_sent": fields.Datetime.now()})
        # 2. Activity log in the lead's chatter (team-visible; the approved
        #    content is OK here — it is our own, human-approved message).
        lead.message_post(
            body=_("Dead-Lead Resurrection: approved WhatsApp comeback sent.\n\n%s",
                   text),
            subtype_xmlid="mail.mt_note")
        # 3. Critical audit (Rule 20) — masked recipient, no raw number/text.
        self.agent._log_critical(
            "send", record=lead,
            after={"event": "dead_lead_send", "channel": CHANNEL,
                   "recipient": self._mask(phone), "chars": len(text)})

    def _audit_fail(self, lead, reason):
        self.agent._log_critical(
            "send", record=lead,
            after={"event": "dead_lead_send", "channel": CHANNEL,
                   "sent": False, "reason": reason})

    @staticmethod
    def _mask(phone):
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())
        if not digits:
            return "wa:?"
        return "wa:%s" % hashlib.sha256(digits.encode("utf-8")).hexdigest()[:12]
