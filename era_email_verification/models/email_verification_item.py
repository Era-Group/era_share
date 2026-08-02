"""One address inside a verification batch, plus its imported raw result."""
from datetime import datetime, timezone

from odoo import api, fields, models

from . import constants


def _parse_iso(value):
    """Parse the API's ISO-8601 timestamp into a naive-UTC datetime for Odoo."""
    if not value:
        return False
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class EmailVerificationItem(models.Model):
    _name = "email.verification.item"
    _description = "Email Verification Item"
    _order = "id desc"
    _rec_name = "email"

    batch_id = fields.Many2one(
        "email.verification.batch", string="Batch", required=True,
        ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="batch_id.company_id", string="Company", store=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Contact", ondelete="set null", index=True)
    mailing_contact_id = fields.Many2one(
        "mailing.contact", string="Mailing Contact", ondelete="set null", index=True)
    email = fields.Char(string="Email (snapshot)", required=True, index=True)

    state = fields.Selection(
        constants.ITEM_STATE_SELECTION, string="Item State",
        default=constants.ITEM_PENDING, required=True, index=True)
    attempts = fields.Integer(string="Attempts", default=0)

    # -- imported raw result --------------------------------------------------
    status = fields.Selection(
        constants.STATUS_SELECTION, string="Result", index=True)
    score = fields.Integer(string="Score")
    reason_summary = fields.Char(string="Reason")
    reason_codes = fields.Char(string="Reason codes")
    catch_all = fields.Boolean(string="Catch-all")
    disposable = fields.Boolean(string="Disposable")
    role_account = fields.Boolean(string="Role account")
    free_provider = fields.Boolean(string="Free provider")
    smtp_code = fields.Char(string="SMTP code")
    smtp_message = fields.Text(string="SMTP message")
    mx_records = fields.Text(string="MX records")
    checked_at = fields.Datetime(string="Checked at")
    elapsed_ms = fields.Integer(string="Elapsed (ms)")
    blacklisted = fields.Boolean(
        string="Added to blacklist", default=False,
        help="Set when this address was added to mail.blacklist by policy.")
    error_message = fields.Text(string="Error")

    _sql_constraints = [
        ("uniq_batch_email", "unique(batch_id, email)",
         "An email may only appear once per verification batch."),
    ]

    @api.model
    def _normalize_result(self, result):
        """Turn a raw API result dict into item field values (+ raw flags)."""
        result = result if isinstance(result, dict) else {}
        flags = result.get("flags")
        if not isinstance(flags, dict):
            flags = {}
        reason_codes = result.get("reason_codes")
        if not isinstance(reason_codes, list):
            reason_codes = []
        reason_codes = [str(rc) for rc in reason_codes]
        mx_records = result.get("mx_records")
        if not isinstance(mx_records, list):
            mx_records = []
        smtp_code = result.get("smtp_code")
        try:
            score = int(result.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        try:
            elapsed = int(result.get("elapsed_ms") or 0)
        except (TypeError, ValueError):
            elapsed = 0
        status = result.get("status")
        if status not in constants.API_STATUSES:
            status = constants.STATUS_UNKNOWN
        return {
            "status": status,
            "score": score,
            "reason_codes": ", ".join(reason_codes),
            "reason_summary": reason_codes[0] if reason_codes else "",
            "catch_all": bool(flags.get("catch_all")),
            "disposable": bool(flags.get("disposable")),
            "role_account": bool(flags.get("role")),
            "free_provider": bool(flags.get("free_provider")),
            "smtp_code": str(smtp_code) if smtp_code is not None else False,
            "smtp_message": result.get("smtp_message") or False,
            "mx_records": ", ".join(str(mx) for mx in mx_records),
            "checked_at": _parse_iso(result.get("checked_at")),
            "elapsed_ms": elapsed,
            "_flags": flags,
        }
