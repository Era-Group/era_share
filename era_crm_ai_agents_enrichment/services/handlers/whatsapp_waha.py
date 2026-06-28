# -*- coding: utf-8 -*-
"""WhatsApp-presence provider — gated + runtime-optional (SCAFFOLD: no check yet).

This is the single most PDPL-sensitive surface in the module: a presence check
transmits an individual's mobile number to WhatsApp/Meta (via the WAHA connector).
It is therefore protected by TWO independent gates, BOTH defaulting to the safe
state, BOTH of which must POSITIVELY pass before any number leaves:

    Gate A — manager toggle ``era_crm_ai_agents_enrichment.whatsapp_check_enabled``
             (default OFF). A missing/unset param is treated as OFF.

    Gate B — per-record consent / lawful basis. Routed through the Compliance
             guard WHEN that module is installed; otherwise it falls back to the
             Base partner consent field ``crm_ai_intl_processing_consent``.

FAIL-CLOSED — READ THIS BEFORE "SIMPLIFYING" THE FALLBACK:
Both gates are fail-closed in EVERY path. If consent cannot be POSITIVELY
confirmed — because Compliance is absent, the guard raises, the consent record is
missing, OR the base field is unset/False — the result is **NO CONSENT**. The
absence of a consent signal is NEVER treated as permission to proceed. The
Compliance-guard route and the base-field fallback are two ways to obtain a
POSITIVE 'yes'; neither is a loophole to skip the gate. A number is transmitted
only when ``_gate_a_enabled()`` AND ``_gate_b_consent()`` BOTH return True AND the
WAHA connector is available.
"""
import hashlib
import logging

from .base import ProviderHandler
from ..waha_adapter import WahaAdapter
from .. import deliverability

_logger = logging.getLogger(__name__)

_NS = "era_crm_ai_agents_enrichment."


class WhatsAppWahaHandler(ProviderHandler):
    provider_type = "whatsapp"

    def capabilities(self):
        return {"whatsapp"}

    def is_available(self):
        """Gate A ON AND the WAHA connector ready. (Gate B is per-record, in enrich.)"""
        return self._gate_a_enabled() and WahaAdapter(self.env).is_available()

    # -- Gate A: manager toggle (fail-closed: unset => OFF) -------------------
    def _gate_a_enabled(self):
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param(_NS + "whatsapp_check_enabled") or "False") == "True"

    # -- Gate B: per-record consent (fail-closed in BOTH paths) --------------
    def _gate_b_consent(self, record):
        """Return True ONLY on a positively-confirmed lawful basis; else False.

        Delegates to the SHARED fail-closed gate (deliverability.consent_ok) so
        WhatsApp and the email/phone verifiers enforce one identical consent rule
        (Compliance guard when present, else the Base consent field; any
        uncertainty -> no consent)."""
        return deliverability.consent_ok(self.env, self._partner_of(record))

    def enrich(self, record, fields_needed):
        """Run the gated WhatsApp-presence check.

        BOTH gates must pass before a number leaves: Gate A (manager toggle) and
        Gate B (per-record consent). The number is transmitted by the WAHA
        connector inside verify_whatsapp; our own audit records only a hashed
        handle + the boolean verdict — never the raw number.
        """
        if not (self._gate_a_enabled() and self._gate_b_consent(record)):
            return self._unknown()
        partner = self._partner_of(record)
        # This CE has no separate mobile field — WhatsApp presence uses `phone`.
        phone = getattr(partner, "phone", False) if partner else False
        res = deliverability.verify_whatsapp(self.env, partner, phone)
        present = res.get("valid")
        if present is not None:
            self.env["crm.ai.enrichment.agent"]._log_critical(
                "external_contact", record=partner,
                after={"channel": "whatsapp", "wa": self._mask_number(phone),
                       "present": bool(present)})
        return {"data": {}, "channel": {"whatsapp": present},
                "status": res.get("status"), "billable": False}

    # -- helpers --------------------------------------------------------------
    @staticmethod
    def _unknown():
        return {"data": {}, "channel": {"whatsapp": None},
                "status": "unknown", "billable": False}

    def _partner_of(self, record):
        if record._name == "res.partner":
            return record
        return getattr(record, "partner_id", False)

    @staticmethod
    def _mask_number(phone):
        """Never log/audit a raw number — a short salted-less hash handle only."""
        if not phone:
            return "<none>"
        return "wa:%s" % hashlib.sha256(phone.encode("utf-8")).hexdigest()[:12]
