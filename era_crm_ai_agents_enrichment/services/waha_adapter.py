# -*- coding: utf-8 -*-
"""Soft, runtime-optional bridge to the WAHA connector (sadeem_waha_whatsapp).

This is NEVER a hard dependency. The connector is detected through the registry
and never imported. If it is absent / uninstalled / not connected, is_available()
is False and a presence check returns None ("unknown") — the waterfall continues,
never errors, never blocks a run.

VENDOR-LOGGING CAVEAT (documented in the Egress Registry): the connector's
``sadeem.waha.session._make_api_request`` logs ``response.text`` on error — vendor
code we do not modify. We therefore pass ONLY the bare phone number to WAHA's
``contacts/check-exists`` (no extra PII), and our OWN audit stays hashed/masked
regardless. The presence check itself transmits the number to WhatsApp/Meta.
"""
import logging

_logger = logging.getLogger(__name__)

# Connector model name + the WAHA "session ready" status (verified against
# sadeem_waha_whatsapp: status Selection includes 'working' = connected/ready).
WAHA_SESSION_MODEL = "sadeem.waha.session"
WAHA_READY_STATUS = "working"
WAHA_CHECK_ENDPOINT = "contacts/check-exists"  # GET ?phone=&session=


class WahaAdapter:
    def __init__(self, env):
        self.env = env

    def is_available(self):
        """True only if (i) the connector model is registered AND (ii) a ready
        session exists. Any uncertainty -> False (fail-closed)."""
        if WAHA_SESSION_MODEL not in self.env:
            return False
        try:
            Session = self.env[WAHA_SESSION_MODEL].sudo()
            return bool(Session.search(
                [("active", "=", True), ("status", "=", WAHA_READY_STATUS)],
                limit=1))
        except Exception:  # noqa: BLE001 — availability probing must never raise
            return False

    def _ready_session(self):
        if WAHA_SESSION_MODEL not in self.env:
            return None
        Session = self.env[WAHA_SESSION_MODEL].sudo()
        return Session.search(
            [("active", "=", True), ("status", "=", WAHA_READY_STATUS)], limit=1)

    def check_presence(self, phone):
        """Return True / False / None (None = unknown / not checked).

        Calls WAHA ``GET /api/contacts/check-exists?phone=&session=`` through the
        connector's OWN vendor seam (``_make_api_request`` — we never touch the
        waha directory). The number IS transmitted to WhatsApp/Meta here; the
        caller MUST have cleared both gates (manager toggle + consent) first.

        Fail-closed: no ready session, an empty number, a vendor error (the seam
        raises UserError on non-200), or an unexpected body all return None —
        'unknown' can never be read as 'present'. We pass ONLY the bare number
        (the vendor logs response.text on error; we add no extra PII), and the
        caller's own audit stays hashed/masked.
        """
        session = self._ready_session()
        if not session:
            return None
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())
        if not digits:
            return None
        try:
            result = session._make_api_request(
                WAHA_CHECK_ENDPOINT, "GET",
                params={"phone": digits, "session": session.session_id})
        except Exception:  # noqa: BLE001 — vendor raises UserError on non-200; unknown
            _logger.info("Enrichment WA: presence check unavailable (fail-closed).")
            return None
        if isinstance(result, dict):
            exists = result.get("numberExists")
            if exists is None:
                exists = result.get("exists")
            return bool(exists) if exists is not None else None
        return None
