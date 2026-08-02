"""Wizard: sweep never-checked / stale contact & mailing-list emails into
verification batches."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Unchecked = has an email AND (never checked OR flagged for re-check).
_UNCHECKED_DOMAIN = [
    ("email", "!=", False),
    "|",
    ("email_verification_status", "in", (False, "not_checked")),
    ("email_verification_needs_recheck", "=", True),
]

_TRUTHY = ("1", "true", "yes", "on")


class VerifyEmailWizard(models.TransientModel):
    _name = "email.verification.wizard"
    _description = "Verify Unchecked Emails"

    scope = fields.Selection(
        [
            ("contacts", "Contacts"),
            ("mailing", "Mailing-list contacts"),
            ("both", "Contacts + mailing-list contacts"),
        ],
        string="Scope", default="both", required=True)
    check_smtp = fields.Boolean(string="Probe SMTP", default=True)
    check_catch_all = fields.Boolean(string="Detect catch-all", default=True)
    limit = fields.Integer(string="Max addresses", default=5000)

    unchecked_partner_count = fields.Integer(
        string="Unchecked contacts", compute="_compute_counts")
    unchecked_contact_count = fields.Integer(
        string="Unchecked mailing contacts", compute="_compute_counts")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Batch = self.env["email.verification.batch"]
        res.setdefault(
            "check_smtp",
            str(Batch._icp("default_check_smtp", "True")).lower() in _TRUTHY)
        res.setdefault(
            "check_catch_all",
            str(Batch._icp("default_check_catch_all", "True")).lower() in _TRUTHY)
        res.setdefault("limit", Batch._batch_size())
        return res

    @api.depends("scope")
    def _compute_counts(self):
        for wiz in self:
            wiz.unchecked_partner_count = (
                self.env["res.partner"].search_count(_UNCHECKED_DOMAIN)
                if wiz.scope in ("contacts", "both") else 0)
            wiz.unchecked_contact_count = (
                self.env["mailing.contact"].search_count(_UNCHECKED_DOMAIN)
                if wiz.scope in ("mailing", "both") else 0)

    def action_verify(self):
        self.ensure_one()
        Batch = self.env["email.verification.batch"]
        limit = self.limit or 5000
        partners = self.env["res.partner"]
        contacts = self.env["mailing.contact"]
        if self.scope in ("contacts", "both"):
            partners = self.env["res.partner"]._ev_find_unchecked(limit=limit)
        if self.scope in ("mailing", "both"):
            contacts = self.env["mailing.contact"]._ev_find_unchecked(limit=limit)

        targets = Batch._prepare_targets(partners=partners, mailing_contacts=contacts)
        if not targets:
            raise UserError(_("No unchecked emails were found for the chosen scope."))
        source = {"contacts": "partners", "mailing": "mailing"}.get(self.scope, "partners")
        batches = Batch._enqueue_targets(
            targets, self.check_smtp, self.check_catch_all, source=source)
        return {
            "type": "ir.actions.act_window",
            "name": _("Verification Batches"),
            "res_model": "email.verification.batch",
            "view_mode": "list,form",
            "domain": [("id", "in", batches.ids)],
        }
