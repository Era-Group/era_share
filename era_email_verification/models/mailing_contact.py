"""mailing.contact verification mirror (lets mailing lists be segmented and
swept). Blacklisting itself is email-based via mail.blacklist, so it protects
mailings regardless of which model an address lives on."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from . import constants


def _norm(email):
    # Strip FIRST so a leading/trailing non-breaking space (U+00A0) can't slip
    # through email_normalize() unchanged and cause an endless re-verify loop
    # (see the matching note in res_partner._norm).
    e = (email or "").strip()
    return email_normalize(e) or e.lower()


class MailingContact(models.Model):
    _inherit = "mailing.contact"

    email_verification_status = fields.Selection(
        constants.STATUS_SELECTION, string="Email Status",
        default=constants.STATUS_NOT_CHECKED, copy=False, index=True)
    email_verification_score = fields.Integer(string="Email Score", copy=False)
    email_verification_checked_email = fields.Char(string="Checked Email", copy=False)
    email_verification_checked_date = fields.Datetime(string="Email Checked On", copy=False)
    email_verification_reason = fields.Char(string="Email Reason", copy=False)
    email_verification_eligible = fields.Boolean(
        string="Mailing-eligible", copy=False, index=True)
    email_verification_needs_recheck = fields.Boolean(
        string="Needs re-check", default=False, copy=False, index=True)
    email_verification_is_stale = fields.Boolean(
        string="Email check is stale",
        compute="_compute_ev_is_stale", search="_search_ev_is_stale",
        help="A previously mailing-eligible address whose check is older "
             "than the configured re-check window (Settings ▸ Email "
             "Verification). Only matters if Auto re-check is enabled.")

    def _compute_ev_is_stale(self):
        cutoff = self._ev_stale_cutoff()
        for contact in self:
            contact.email_verification_is_stale = bool(
                contact.email_verification_eligible
                and not contact.email_verification_needs_recheck
                and contact.email_verification_checked_date
                and contact.email_verification_checked_date < cutoff)

    def _search_ev_is_stale(self, operator, value):
        cutoff = self._ev_stale_cutoff()
        if constants.wants_true(operator, value):
            return [
                ("email_verification_eligible", "=", True),
                ("email_verification_needs_recheck", "=", False),
                ("email_verification_checked_date", "!=", False),
                ("email_verification_checked_date", "<", cutoff),
            ]
        return [
            "|", ("email_verification_eligible", "!=", True),
            "|", ("email_verification_needs_recheck", "!=", False),
            "|", ("email_verification_checked_date", "=", False),
            ("email_verification_checked_date", ">=", cutoff),
        ]

    @api.model
    def _ev_stale_cutoff(self):
        Batch = self.env["email.verification.batch"]
        return fields.Datetime.subtract(fields.Datetime.now(), days=Batch._stale_days())

    def write(self, vals):
        res = super().write(vals)
        if "email" in vals and not self.env.context.get("ev_apply_result"):
            for contact in self:
                snap = contact.email_verification_checked_email
                if snap and _norm(contact.email) != _norm(snap):
                    super(MailingContact, contact).write({
                        "email_verification_status": constants.STATUS_NOT_CHECKED,
                        "email_verification_score": 0,
                        "email_verification_reason": False,
                        "email_verification_eligible": False,
                        "email_verification_checked_email": False,
                        "email_verification_checked_date": False,
                        "email_verification_needs_recheck": True,
                    })
        return res

    @api.model
    def _ev_find_unchecked(self, limit=5000):
        return self.search([
            ("email", "!=", False),
            "|",
            ("email_verification_status", "in", (False, constants.STATUS_NOT_CHECKED)),
            ("email_verification_needs_recheck", "=", True),
        ], limit=limit)

    @api.model
    def _ev_find_stale(self, limit=5000):
        """Previously mailing-eligible contacts whose check has aged past the
        configured stale-days window (opt-in periodic re-check)."""
        return self.search(
            [("email", "!=", False), ("email_verification_is_stale", "=", True)],
            limit=limit)

    def action_verify_email_bulk(self):
        contacts = self.filtered(lambda c: c.email)
        if not contacts:
            raise UserError(_("None of the selected contacts has an email address."))
        Batch = self.env["email.verification.batch"]
        targets = Batch._prepare_targets(mailing_contacts=contacts)
        batches = Batch._enqueue_targets(targets, source="mailing")
        return {
            "type": "ir.actions.act_window",
            "name": _("Verification Batches"),
            "res_model": "email.verification.batch",
            "view_mode": "list,form",
            "domain": [("id", "in", batches.ids)],
        }
