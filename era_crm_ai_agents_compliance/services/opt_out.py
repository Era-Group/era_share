# -*- coding: utf-8 -*-
"""Opt-out handling within the PDPL 72-hour legal window.

Two entry points (the task's named functions):
  - ``process_opt_out(env, partner)`` — applies an opt-out *immediately*:
    appends a 'withdrawn' marketing-consent event (task 1.1), clears the Base
    ``crm_ai_intl_processing_consent`` flag the AI Compliance Guard reads, stamps
    the request time, and audits it (Rule 20). Because it is synchronous, the
    72-hour SLA is satisfied the moment it runs.
  - ``cron_enforce_72h(env)`` — a daily SAFETY NET. It reconciles any contact
    that carries an opt-out request but is somehow still marked as consented
    (e.g. a request recorded by some other channel without processing), forcing
    the withdrawal and flagging an SLA breach if one slipped past 72h.

Both are plain functions taking ``env`` so they are easy to unit-test; the
res.partner model exposes thin wrappers so the ir.cron and UI can reach them.

Permissions: these functions perform no sudo of their own — they act with
whatever env they are handed. The cron hands them the root cron env; the public
opt-out controller hands them a deliberately, narrowly-scoped sudo env (the
approved opt-out elevation), because the opting-out recipient is not logged in.
"""
import hashlib
import hmac
import logging

from odoo import fields

from .compliance_config import ComplianceConfig

_logger = logging.getLogger(__name__)

# Fallback only; the live value is the configurable opt_out_window_hours setting.
SLA_HOURS = 72


# ---------------------------------------------------------------------------
# Signed opt-out token (no new stored secret: reuses Odoo's instance secret)
# ---------------------------------------------------------------------------
def _token(env, partner_id):
    secret = (env["ir.config_parameter"].sudo().get_param("database.secret") or "").encode()
    msg = ("crm_ai_opt_out:%s" % partner_id).encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def make_token(env, partner):
    """Token to embed in an unsubscribe link for *partner*."""
    pid = partner.id if hasattr(partner, "_name") else int(partner)
    return _token(env, pid)


def verify_token(env, partner_id, token):
    """Constant-time check of an opt-out token."""
    return hmac.compare_digest(_token(env, int(partner_id)), token or "")


# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------
def process_opt_out(env, partner, source="manual"):
    """Apply an opt-out immediately. Returns True on success, False if the
    partner no longer exists."""
    partner = partner if hasattr(partner, "_name") else env["res.partner"].browse(int(partner))
    if not partner.exists():
        return False

    # 1. Append the withdrawal to the consent log (also audits the withdrawal).
    env["crm.ai.consent"].register_consent(
        partner, consent_type="marketing", state="withdrawn", source=source,
    )
    # 2. Clear the Base consent flag the guard reads + stamp the request time.
    vals = {"crm_ai_intl_processing_consent": False}
    if not partner.crm_ai_opt_out_requested_on:
        vals["crm_ai_opt_out_requested_on"] = fields.Datetime.now()
    partner.write(vals)
    # 3. Audit the opt-out itself (Rule 20).
    env["crm.ai.audit.log"].log(
        "other", record=partner,
        after={"event": "opt_out_processed", "source": source},
    )
    return True


def cron_enforce_72h(env):
    """Safety net: force-withdraw any contact that requested opt-out but is
    still consented, and flag any that slipped past the 72h SLA. Returns the
    number of contacts enforced."""
    Partner = env["res.partner"]
    Consent = env["crm.ai.consent"]
    window_hours = ComplianceConfig(env).i("opt_out_window_hours", SLA_HOURS)
    pending = Partner.search([("crm_ai_opt_out_requested_on", "!=", False)])
    now = fields.Datetime.now()
    enforced = 0
    for partner in pending:
        still_consented = (
            Consent.has_consent(partner, "marketing")
            or partner.crm_ai_intl_processing_consent
        )
        if not still_consented:
            continue
        age_hours = (now - partner.crm_ai_opt_out_requested_on).total_seconds() / 3600.0
        if age_hours > window_hours:
            env["crm.ai.audit.log"].log(
                "other", record=partner,
                after={"event": "opt_out_sla_breach", "age_hours": round(age_hours, 1)},
            )
        process_opt_out(env, partner, source="cron-72h-enforce")
        enforced += 1
    _logger.info("cron_enforce_72h: enforced %s pending opt-out(s)", enforced)
    return enforced
