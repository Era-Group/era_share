# -*- coding: utf-8 -*-
"""res.partner — the manual "Enrich" entry point + deliverability result flags.

The three booleans are WRITTEN BY THE ENGINE from a verification run (task 2.4),
never edited by hand — hence readonly. They are deliberately TRI-STATE-LOSSY: a
Boolean cannot distinguish "verified invalid" from "never checked". Use
``crm_ai_deliverability_checked`` (empty = never run) to tell the two apart, and
the per-run ``crm.ai.enrichment.request.contactability`` JSON (which keeps
None = unknown) for the authoritative per-channel verdict.
"""
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Deliverability flags — set by crm.ai.enrichment only on a DEFINITE verdict
    # (an 'unknown' result leaves the prior value untouched).
    email_valid = fields.Boolean(
        string="Email deliverable", readonly=True, copy=False,
        help="Set by the Enrichment engine from an email-verification provider "
             "(e.g. ZeroBounce). See 'Deliverability checked' for recency.")
    mobile_valid = fields.Boolean(
        string="Phone reachable", readonly=True, copy=False,
        help="Set by the Enrichment engine from a phone/HLR-lookup provider. "
             "Verifies the partner's phone number (this CE has no separate "
             "mobile field).")
    has_whatsapp = fields.Boolean(
        string="On WhatsApp", readonly=True, copy=False,
        help="Set by the Enrichment engine from the gated, consent-checked "
             "WhatsApp-presence provider (WAHA). False can mean 'not present' "
             "OR 'never checked' — see 'Deliverability checked'.")
    crm_ai_deliverability_checked = fields.Datetime(
        string="Deliverability checked", readonly=True, copy=False,
        help="When the last deliverability verification run wrote a flag on this "
             "record. Empty = never verified.")

    def action_crm_ai_enrich(self):
        """Trigger an enrichment pass for this contact (runs as the caller)."""
        self.ensure_one()
        return self.env["crm.ai.enrichment.agent"].enrich(self)
