"""res.partner verification fields, single verify, and email-change reset."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from . import constants


def _norm(email):
    # Strip FIRST: email_normalize() returns a leading/trailing non-breaking
    # space (U+00A0) address unchanged-and-truthy (e.g. "\xa0a@b.com"), which
    # defeats the .lower() fallback and makes an item's normalized email differ
    # from its own partner's -> the result is marked stale and the address is
    # re-verified forever. .strip() removes unicode whitespace incl. NBSP.
    e = (email or "").strip()
    return email_normalize(e) or e.lower()


class ResPartner(models.Model):
    _inherit = "res.partner"

    email_verification_status = fields.Selection(
        constants.STATUS_SELECTION, string="Email Status",
        default=constants.STATUS_NOT_CHECKED, copy=False, index=True, tracking=True)
    email_verification_score = fields.Integer(string="Email Score", copy=False)
    email_verification_checked_email = fields.Char(
        string="Checked Email", copy=False,
        help="The exact address that produced the current status. If the "
             "contact's email later differs from this, the status is reset.")
    email_verification_checked_date = fields.Datetime(string="Email Checked On", copy=False)
    email_verification_reason = fields.Char(string="Email Reason", copy=False)
    email_verification_catch_all = fields.Boolean(string="Catch-all domain", copy=False)
    email_verification_disposable = fields.Boolean(string="Disposable", copy=False)
    email_verification_role = fields.Boolean(string="Role account", copy=False)
    email_verification_free_provider = fields.Boolean(string="Free provider", copy=False)
    email_verification_smtp_code = fields.Char(string="SMTP code", copy=False)
    email_verification_eligible = fields.Boolean(
        string="Mailing-eligible", copy=False, index=True,
        help="Technically safe to email: a deliverable, non-disposable address "
             "scoring above the configured threshold. Not a proof of consent.")
    email_verification_needs_recheck = fields.Boolean(
        string="Needs re-check", default=False, copy=False, index=True)
    email_verification_is_stale = fields.Boolean(
        string="Email check is stale",
        compute="_compute_ev_is_stale", search="_search_ev_is_stale",
        help="A previously mailing-eligible address whose check is older "
             "than the configured re-check window (Settings ▸ Email "
             "Verification). Only matters if Auto re-check is enabled.")

    email_verification_item_ids = fields.One2many(
        "email.verification.item", "partner_id", string="Verification history")
    email_verification_history_count = fields.Integer(
        string="Verification count", compute="_compute_ev_history_count")

    # -- computes -------------------------------------------------------------
    def _compute_ev_history_count(self):
        data = self.env["email.verification.item"]._read_group(
            [("partner_id", "in", self.ids)], ["partner_id"], ["__count"])
        counts = {partner.id: count for partner, count in data}
        for partner in self:
            partner.email_verification_history_count = counts.get(partner.id, 0)

    def _compute_ev_is_stale(self):
        cutoff = self._ev_stale_cutoff()
        for partner in self:
            partner.email_verification_is_stale = bool(
                partner.email_verification_eligible
                and not partner.email_verification_needs_recheck
                and partner.email_verification_checked_date
                and partner.email_verification_checked_date < cutoff)

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

    # -- email-change reset ---------------------------------------------------
    def write(self, vals):
        res = super().write(vals)
        if "email" in vals and not self.env.context.get("ev_apply_result"):
            for partner in self:
                snap = partner.email_verification_checked_email
                if snap and _norm(partner.email) != _norm(snap):
                    super(ResPartner, partner).write(self._ev_reset_vals())
        return res

    @api.model
    def _ev_reset_vals(self):
        return {
            "email_verification_status": constants.STATUS_NOT_CHECKED,
            "email_verification_score": 0,
            "email_verification_reason": False,
            "email_verification_eligible": False,
            "email_verification_catch_all": False,
            "email_verification_disposable": False,
            "email_verification_role": False,
            "email_verification_free_provider": False,
            "email_verification_smtp_code": False,
            "email_verification_checked_email": False,
            "email_verification_checked_date": False,
            "email_verification_needs_recheck": True,
        }

    # -- unchecked / stale selection (for auto re-check cron) -----------------
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

    # -- single synchronous verify (form button) ------------------------------
    def action_verify_email(self):
        self.ensure_one()
        if not self.email:
            raise UserError(_("This contact has no email address to verify."))
        Batch = self.env["email.verification.batch"]
        check_smtp = str(Batch._icp("default_check_smtp", "True")).lower() in (
            "1", "true", "yes", "on")
        check_catch_all = str(Batch._icp("default_check_catch_all", "True")).lower() in (
            "1", "true", "yes", "on")
        result = self.env["email.verification.client"].verify_one(
            self.email, check_smtp=check_smtp, check_catch_all=check_catch_all)

        now = fields.Datetime.now()
        batch = Batch.create({
            "source": "manual", "state": "done", "total_count": 1,
            "import_offset": 1, "check_smtp": check_smtp,
            "check_catch_all": check_catch_all,
            "date_submitted": now, "date_done": now,
        })
        Item = self.env["email.verification.item"]
        item = Item.create({
            "batch_id": batch.id, "email": (self.email or "").strip(),
            "partner_id": self.id,
        })
        vals = Item._normalize_result(result)
        flags = vals.pop("_flags")
        item.write(dict(vals, state=constants.ITEM_DONE, attempts=1))
        batch._write_targets(item, vals, flags,
                             batch._blacklist_policy(), batch._min_eligible_score())
        batch._recompute_counts()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": constants.STATUS_DECORATION.get(vals["status"], "info"),
                "title": _("Email verified"),
                "message": _("Status: %(status)s (score %(score)s).") % {
                    "status": vals["status"], "score": vals["score"]},
                "sticky": False,
                # Returning a client action leaves the form showing the values
                # it loaded before the check, so the badge would still read
                # "Not checked" until a manual refresh. display_notification
                # chains whatever is in "next", so reload the view behind the
                # message.
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    # -- bulk async verify (list contextual action) ---------------------------
    def action_verify_email_bulk(self):
        partners = self.filtered(lambda p: p.email)
        if not partners:
            raise UserError(_("None of the selected contacts has an email address."))
        Batch = self.env["email.verification.batch"]
        targets = Batch._prepare_targets(partners=partners)
        batches = Batch._enqueue_targets(targets, source="partners")
        return {
            "type": "ir.actions.act_window",
            "name": _("Verification Batches"),
            "res_model": "email.verification.batch",
            "view_mode": "list,form",
            "domain": [("id", "in", batches.ids)],
        }

    def action_open_verification_history(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Verification History"),
            "res_model": "email.verification.item",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"search_default_group_status": 1},
        }
