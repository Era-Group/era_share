# -*- coding: utf-8 -*-
"""Deliverability verification primitives (task 2.4).

Three pure functions, each returning the SAME uniform verdict dict so the
provider handlers can stay thin:

    {"valid": True | False | None,   # channel verdict; None = unknown / not checked
     "billable": bool,               # did a real vendor call actually go out?
     "status": "success" | "unknown" | "error"}

    verify_email(env, partner, email, api_key_env)   -> ZeroBounce (HTTP egress)
    verify_mobile(env, partner, phone, api_key_env)  -> HLR lookup (HTTP egress)
    verify_whatsapp(env, partner, phone)             -> WAHA presence (connector)

PDPL — CROSS-BORDER PII EGRESS IS CONSENT-GATED (fail-closed):
Every function transmits a real person's email/phone to an external vendor, so a
value leaves ONLY when ``consent_ok()`` POSITIVELY confirms a lawful basis. Any
uncertainty -> no consent -> no call -> 'unknown' (never billable). Tokens are read
from ``os.getenv`` at call time by NAME (Rule 03 — the value is never stored); the
HTTP itself goes through the agent's single egress seam (``_http_get``) so a
failure logs host + exception class only, never the URL/query (which carries the
token).
"""
import logging
import os

_logger = logging.getLogger(__name__)

_NS = "era_crm_ai_agents_enrichment."

# Compliance (#1) is DETECTED, never imported. Its consent-log model:
_COMPLIANCE_CONSENT_MODEL = "crm.ai.consent"

# Default vendor endpoints. Overridable via ir.config_parameter (the override is
# a URL, never a token). ZeroBounce's endpoint is stable/canonical; HLR has no
# single canonical vendor, so it MUST be configured (absent -> 'unknown').
_ZEROBOUNCE_URL = "https://api.zerobounce.net/v2/validate"

# ZeroBounce 'status' -> tri-state verdict. 'catch-all'/'unknown' are genuinely
# indeterminate, so they stay None (lets the waterfall try another verifier).
_ZB_VALID = {"valid"}
_ZB_INVALID = {"invalid", "do_not_mail", "spamtrap", "abuse"}


def _unknown():
    return {"valid": None, "billable": False, "status": "unknown"}


# ---------------------------------------------------------------------------
# Consent — shared, fail-closed lawful-basis gate (also used by the WA handler)
# ---------------------------------------------------------------------------
def consent_ok(env, partner):
    """True ONLY on a positively-confirmed lawful basis for cross-border PII.

    Compliance guard (crm.ai.consent) when that module is installed; otherwise
    the Base partner field crm_ai_intl_processing_consent. ANY uncertainty (no
    subject, guard raises, record missing, field unset/False) -> False. The
    absence of a consent signal is NEVER treated as permission.
    """
    if not partner:
        return False
    if _COMPLIANCE_CONSENT_MODEL in env:
        try:
            return bool(_compliance_consent(env, partner))
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Enrichment: Compliance consent check raised; treating as NO "
                "consent (fail-closed).")
            return False
    return bool(partner.crm_ai_intl_processing_consent)


def _compliance_consent(env, partner):
    """Positive consent via the Compliance layer. Filled in when that module's
    real check lands; until then -> False (fail-closed)."""
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _cfg(env, key, default=None):
    return env["ir.config_parameter"].sudo().get_param(_NS + key, default)


def _agent(env):
    """The enrichment agent — owner of the single non-LLM egress seam."""
    return env["crm.ai.enrichment.agent"]


def _digits(value):
    """MSISDN as bare digits (drop spaces, dashes, parens, leading +)."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


# ---------------------------------------------------------------------------
# Email — ZeroBounce
# ---------------------------------------------------------------------------
def verify_email(env, partner, email, api_key_env):
    if not email or not consent_ok(env, partner):
        return _unknown()
    token = os.getenv(api_key_env or "")
    if not token:
        return _unknown()  # provider active but token not provisioned -> no call
    url = _cfg(env, "zerobounce_url") or _ZEROBOUNCE_URL
    resp = _agent(env)._http_get(url, params={"api_key": token, "email": email})
    if resp is None:
        return {"valid": None, "billable": True, "status": "error"}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 — a malformed body is an error, not a verdict
        return {"valid": None, "billable": True, "status": "error"}
    status = (data.get("status") or "").lower()
    if status in _ZB_VALID:
        verdict = True
    elif status in _ZB_INVALID:
        verdict = False
    else:
        verdict = None  # catch-all / unknown
    return {"valid": verdict, "billable": True, "status": "success"}


# ---------------------------------------------------------------------------
# Phone — HLR lookup (generic; endpoint is config-driven, no vendor hardcoded)
# ---------------------------------------------------------------------------
def verify_mobile(env, partner, phone, api_key_env):
    if not phone or not consent_ok(env, partner):
        return _unknown()
    token = os.getenv(api_key_env or "")
    if not token:
        return _unknown()
    # No canonical HLR vendor is pinned by the project — the endpoint is set by a
    # manager via this param. Absent -> we cannot verify -> 'unknown' (no call).
    url = _cfg(env, "hlr_url")
    if not url:
        return _unknown()
    resp = _agent(env)._http_get(
        url, params={"key": token, "number": _digits(phone)})
    if resp is None:
        return {"valid": None, "billable": True, "status": "error"}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {"valid": None, "billable": True, "status": "error"}
    verdict = _hlr_verdict(data)
    return {"valid": verdict, "billable": True, "status": "success"}


def _hlr_verdict(data):
    """Map a generic HLR JSON body to True/False/None, defensively.

    Accepts the common shapes: an explicit boolean ('valid'/'live'/'reachable')
    or a status string ('connected'/'active'/'live' -> reachable; 'absent'/
    'disconnected'/'invalid'/'unknown' -> not / indeterminate)."""
    if not isinstance(data, dict):
        return None
    for key in ("valid", "live", "reachable"):
        if key in data and isinstance(data[key], bool):
            return data[key]
    status = str(data.get("status") or data.get("hlr_status") or "").lower()
    if status in {"connected", "active", "live", "reachable", "valid"}:
        return True
    if status in {"absent", "disconnected", "invalid", "unreachable", "dead"}:
        return False
    return None


# ---------------------------------------------------------------------------
# WhatsApp — WAHA presence (runtime-optional connector; never billable)
# ---------------------------------------------------------------------------
def verify_whatsapp(env, partner, phone):
    """Consent + WAHA presence. Gate A (manager toggle) is enforced UPSTREAM by
    the handler; this re-checks consent (defense in depth) and never bills."""
    if not phone or not consent_ok(env, partner):
        return _unknown()
    from .waha_adapter import WahaAdapter
    present = WahaAdapter(env).check_presence(phone)
    status = "success" if present is not None else "unknown"
    return {"valid": present, "billable": False, "status": status}
