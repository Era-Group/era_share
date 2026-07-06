# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CrmAiCampaignSuppression(models.Model):
    """The never-email list. Any partner matching an ACTIVE entry here is
    excluded from every campaign, always — this check is absolute and runs
    both at selection time and again at the final hand-off.

    An entry matches by partner (exact record) and/or by email pattern:
    a full address ('someone@acme.com') or a domain ('acme.com' or
    '@acme.com'), compared case-insensitively against the partner's email.
    """

    _name = "crm.ai.campaign.suppression"
    _description = "CRM AI Campaign Suppression List"
    _order = "id desc"

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        ondelete="cascade",
        help="Suppress exactly this partner. Optional — an entry may instead "
             "(or additionally) match by email pattern.",
    )
    email_pattern = fields.Char(
        string="Email / Domain Pattern",
        help="A full email address ('someone@acme.com') or a domain "
             "('acme.com' / '@acme.com'). Matched case-insensitively against "
             "the partner's email address.",
    )
    reason = fields.Char(
        help="Why this entry exists (complaint, legal request, bounce, ...). "
             "Keep it reference-only — no free-text personal data beyond what "
             "the entry itself needs.",
    )
    active = fields.Boolean(default=True)

    @api.constrains("partner_id", "email_pattern")
    def _check_has_target(self):
        for entry in self:
            if not entry.partner_id and not (entry.email_pattern or "").strip():
                raise ValidationError(_(
                    "A suppression entry needs a partner or an email pattern "
                    "(or both) — an empty entry would suppress nothing."))

    # ------------------------------------------------------------------
    @api.model
    def _matches(self, partner):
        """True if *partner* is covered by any active suppression entry."""
        if self.search_count([("partner_id", "=", partner.id)], limit=1):
            return True
        email = (partner.email or "").strip().lower()
        if not email:
            return False
        domain_part = email.split("@", 1)[1] if "@" in email else ""
        for entry in self.search([("email_pattern", "!=", False)]):
            pattern = (entry.email_pattern or "").strip().lower().lstrip("@")
            if not pattern:
                continue
            if "@" in pattern:
                if email == pattern:
                    return True
            elif domain_part and (domain_part == pattern
                                  or domain_part.endswith("." + pattern)):
                return True
        return False
