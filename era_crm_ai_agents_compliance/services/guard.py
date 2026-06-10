# -*- coding: utf-8 -*-
"""Unified compliance gate — the single ``guard()`` every sending agent calls
before a message goes out.

It composes the three compliance bricks built earlier in this module:
  1. PDPL consent      (crm.ai.consent.has_consent)   — task 1.1
  2. send-window       (services.send_window.SendWindow) — task 1.3
  3. cultural norms    (services.norms.CulturalNorms)  — task 1.4

and returns a single decision ``{allowed, reason, deferred_until}``. Every call
— allowed or blocked — writes exactly one entry to the Base critical audit log
(Rule 20). The guard runs under the calling user's permissions (Rule 09 / 19);
the only elevation reached is the audit log's own create-only sudo.

Check order is legal-strictness first: no consent is an absolute stop; a wrong
time merely defers (deferred_until = next allowed slot); a norms problem blocks
the auto-send so the agent can route it to human edit/approval.
"""
import logging

from odoo import fields

from .send_window import SendWindow
from .norms import CulturalNorms

_logger = logging.getLogger(__name__)


class ComplianceGuard:
    """Instantiate with the calling environment: ``ComplianceGuard(env)``."""

    def __init__(self, env):
        self.env = env

    # ------------------------------------------------------------------
    def guard(self, partner, text, channel=None, consent_type="marketing"):
        """Return the compliance decision for sending *text* to *partner* over
        *channel*.

        :returns: ``{"allowed": bool, "reason": str, "deferred_until": datetime|None}``
            ``deferred_until`` is a naive UTC datetime, set only when the block
            is purely a timing one (so the caller can reschedule).
        """
        partner_rec = self._as_partner(partner)
        decision = {"allowed": True, "reason": "allowed", "deferred_until": None}

        # 1. PDPL consent — absolute stop, no defer.
        if not self.env["crm.ai.consent"].has_consent(partner_rec, consent_type):
            decision = {
                "allowed": False,
                "reason": "no %s consent on file (PDPL)" % consent_type,
                "deferred_until": None,
            }
        else:
            # 2. Send-window — a timing block; offer the next allowed slot.
            now = fields.Datetime.now()
            partner_tz = partner_rec.tz or None
            window = SendWindow(self.env)
            allowed, reason = window.is_send_allowed(now, partner_tz)
            if not allowed:
                decision = {
                    "allowed": False,
                    "reason": "send window: %s" % reason,
                    "deferred_until": window.next_allowed_slot(now, partner_tz),
                }
            else:
                # 3. Cultural norms — content block, routes to human edit.
                ok, issues = CulturalNorms(self.env).check_norms(text)
                if not ok:
                    decision = {
                        "allowed": False,
                        "reason": "cultural norms: %s" % ", ".join(issues),
                        "deferred_until": None,
                    }

        self._audit(partner_rec, channel, decision)
        return decision

    # ------------------------------------------------------------------
    def _audit(self, partner, channel, decision):
        """Write exactly one audit entry for this guard decision (Rule 20)."""
        deferred = decision["deferred_until"]
        self.env["crm.ai.audit.log"].log(
            "blocked" if not decision["allowed"] else "other",
            record=partner if partner else None,
            after={
                "event": "compliance_guard",
                "channel": channel,
                "allowed": decision["allowed"],
                "reason": decision["reason"],
                "deferred_until": fields.Datetime.to_string(deferred) if deferred else None,
            },
        )

    def _as_partner(self, partner):
        if hasattr(partner, "_name"):
            return partner
        return self.env["res.partner"].browse(int(partner))


def guard(env, partner, text, channel=None, consent_type="marketing"):
    """Functional convenience: ``guard(env, partner, text, channel)``."""
    return ComplianceGuard(env).guard(partner, text, channel, consent_type)
