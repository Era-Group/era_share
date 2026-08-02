"""Settings for the Email Verification module.

All values are stored as ``ir.config_parameter`` entries. The API key and base
URL are restricted to system administrators (``base.group_system``) and the key
is never echoed back to a non-admin nor logged anywhere.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

_SYSTEM = "base.group_system"

# Boolean settings whose "off" state has to be persisted explicitly — see
# ``_ev_persist_boolean_params``.
_BOOLEAN_PARAM_FIELDS = (
    "era_ev_verify_tls",
    "era_ev_default_check_smtp",
    "era_ev_default_check_catch_all",
    "era_ev_auto_recheck",
    "era_ev_push_enabled",
    "era_ev_blacklist_undeliverable",
    "era_ev_blacklist_risky",
    "era_ev_blacklist_disposable",
    "era_ev_blacklist_unknown",
    "era_ev_blacklist_catch_all",
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # -- connection (system administrators only) ------------------------------
    era_ev_base_url = fields.Char(
        string="Verifier Base URL", groups=_SYSTEM,
        config_parameter="era_email_verification.base_url",
        help="HTTPS base URL of the private Email Verifier API, e.g. "
             "https://email-validation.letsw.com . Plain http:// is only "
             "accepted for localhost.")
    era_ev_api_key = fields.Char(
        string="Verifier API Key", groups=_SYSTEM,
        config_parameter="era_email_verification.api_key",
        help="Bearer key sent as 'Authorization: Bearer <key>'. Stored in "
             "ir.config_parameter, restricted to administrators, and never "
             "logged or returned.")
    era_ev_verify_tls = fields.Boolean(
        string="Verify TLS certificate", groups=_SYSTEM, default=True,
        config_parameter="era_email_verification.verify_tls",
        help="Validate the verifier's TLS certificate. Keep enabled in "
             "production; disable only against a trusted internal host.")
    era_ev_timeout_connect = fields.Integer(
        string="Connect timeout (s)", groups=_SYSTEM, default=5,
        config_parameter="era_email_verification.timeout_connect")
    era_ev_timeout_read = fields.Integer(
        string="Read timeout (s)", groups=_SYSTEM, default=30,
        config_parameter="era_email_verification.timeout_read")

    # -- batching / probing ---------------------------------------------------
    era_ev_batch_size = fields.Integer(
        string="Max emails per batch", groups=_SYSTEM, default=5000,
        config_parameter="era_email_verification.batch_size",
        help="Upper bound on addresses per remote job. The verifier caps this "
             "at 5,000; larger selections are split into several batches.")
    era_ev_default_check_smtp = fields.Boolean(
        string="Probe SMTP by default", groups=_SYSTEM, default=True,
        config_parameter="era_email_verification.default_check_smtp",
        help="When on, verification connects to the mail server and issues an "
             "RCPT probe (strongest signal). When off, only DNS/MX is checked.")
    era_ev_default_check_catch_all = fields.Boolean(
        string="Detect catch-all by default", groups=_SYSTEM, default=True,
        config_parameter="era_email_verification.default_check_catch_all",
        help="Probe a random address to detect catch-all domains that accept "
             "everything. Skipped automatically for known free providers.")

    # -- eligibility & re-check -----------------------------------------------
    era_ev_min_eligible_score = fields.Integer(
        string="Min score to be mailing-eligible", groups=_SYSTEM, default=80,
        config_parameter="era_email_verification.min_eligible_score",
        help="A deliverable, non-disposable address scoring at least this value "
             "is flagged eligible for mailing. Default 80.")
    era_ev_stale_days = fields.Integer(
        string="Re-check after (days)", groups=_SYSTEM, default=180,
        config_parameter="era_email_verification.stale_days",
        help="A previously-checked address older than this many days is "
             "considered stale and eligible for an automatic re-check.")
    era_ev_auto_recheck = fields.Boolean(
        string="Auto re-check unchecked / stale", groups=_SYSTEM, default=False,
        config_parameter="era_email_verification.auto_recheck",
        help="When on, a scheduled action periodically queues never-checked and "
             "stale mailing-eligible contacts for verification.")

    # -- result delivery (push webhook + poll fallback) -----------------------
    era_ev_push_enabled = fields.Boolean(
        string="Push results (webhook)", groups=_SYSTEM, default=True,
        config_parameter="era_email_verification.push_enabled",
        help="When on, the verifier pushes completed results to this Odoo "
             "instance as they finish (low latency). A poll still reconciles as "
             "a fallback. Requires a public HTTPS URL for Odoo (below).")
    era_ev_callback_base_url = fields.Char(
        string="Public Odoo URL for push", groups=_SYSTEM,
        config_parameter="era_email_verification.callback_base_url",
        help="HTTPS base URL the verifier can reach this Odoo at, e.g. "
             "https://odoo.example.com . Defaults to the system 'web.base.url'. "
             "Push is only offered over HTTPS.")
    era_ev_reconcile_stale_minutes = fields.Integer(
        string="Fallback poll after (min)", groups=_SYSTEM, default=5,
        config_parameter="era_email_verification.reconcile_stale_minutes",
        help="If no push has arrived for a running batch within this many "
             "minutes, the fallback poll pulls its results instead. Keep it "
             "below the scheduled action's interval (default: poll after 5 "
             "min, action runs every 15) — if the two are equal a batch waits "
             "two runs instead of one.")
    era_ev_stuck_batch_hours = fields.Integer(
        string="Give up on a stuck batch after (h)", groups=_SYSTEM, default=6,
        config_parameter="era_email_verification.stuck_batch_hours",
        help="A running batch that imports no new result at all for this many "
             "hours is marked failed and its remote job deleted. Only one job "
             "runs at a time, so this keeps a hung job from blocking the "
             "queue forever. A batch that is still making progress is never "
             "affected, however long it takes.")

    # -- blacklist policy (one independent checkbox per outcome) --------------
    era_ev_blacklist_undeliverable = fields.Boolean(
        string="Undeliverable", groups=_SYSTEM, default=True,
        config_parameter="era_email_verification.blacklist_undeliverable",
        help="Addresses the mail server rejected or whose mailbox does not "
             "exist — a guaranteed hard bounce. The safest box to keep on.")
    era_ev_blacklist_risky = fields.Boolean(
        string="Risky", groups=_SYSTEM, default=True,
        config_parameter="era_email_verification.blacklist_risky",
        help="Accepted but uncertain addresses, such as role accounts (info@, "
             "sales@, support@…) and mailboxes reported as full. Catch-all and "
             "disposable have their own boxes below.")
    era_ev_blacklist_disposable = fields.Boolean(
        string="Disposable (temporary domains)", groups=_SYSTEM, default=False,
        config_parameter="era_email_verification.blacklist_disposable",
        help="Throwaway/temporary domains such as mailinator.com. Ticking "
             "Risky already covers them; use this to blacklist them without "
             "blacklisting the other risky addresses.")
    era_ev_blacklist_unknown = fields.Boolean(
        string="Unknown", groups=_SYSTEM, default=False,
        config_parameter="era_email_verification.blacklist_unknown",
        help="Addresses that could not be determined (greylisting, timeouts, "
             "providers that block probing). The most aggressive option: it "
             "can blacklist good addresses that were only temporarily "
             "unreachable, so pair it with Auto re-check.")
    era_ev_blacklist_catch_all = fields.Boolean(
        string="Catch-all (accept-all domains)", groups=_SYSTEM, default=False,
        config_parameter="era_email_verification.blacklist_catch_all",
        help="A catch-all domain accepts every address, so a 'risky' result "
             "there means the mailbox could not be confirmed — NOT that it is "
             "invalid. A valid mailbox can live on such a domain, so this box "
             "is independent of Risky and off by default.")

    # -- validation -----------------------------------------------------------
    def set_values(self):
        for record in self:
            if record.era_ev_base_url:
                # Reuse the client's SSRF/scheme validation.
                self.env["email.verification.client"]._validate_base_url(
                    record.era_ev_base_url.strip().rstrip("/"))
            if record.era_ev_batch_size and record.era_ev_batch_size > 5000:
                raise UserError(_("The maximum batch size is 5,000."))
            if record.era_ev_batch_size and record.era_ev_batch_size < 1:
                raise UserError(_("The batch size must be at least 1."))
            if record.era_ev_min_eligible_score and not (
                    0 <= record.era_ev_min_eligible_score <= 100):
                raise UserError(_("The eligibility score must be between 0 and 100."))
        res = super().set_values()
        self._ev_persist_boolean_params()
        return res

    def _ev_persist_boolean_params(self):
        """Store every boolean setting explicitly, including when it is off.

        ``res.config.settings`` writes a False boolean by *deleting* the
        parameter (``set_param`` unlinks on a falsy value), and a missing
        parameter reads back as the field's ``default``. Any box defaulting to
        True — Undeliverable, Risky, Verify TLS, the two probe defaults, Push —
        would therefore silently switch itself back on after being unticked.
        Writing "False" makes the off state stick.
        """
        if not self.env.user.has_group(_SYSTEM):
            # The fields are system-only; a non-admin never edited them and
            # cannot read them here.
            return
        icp = self.env["ir.config_parameter"].sudo()
        for record in self:
            for name in _BOOLEAN_PARAM_FIELDS:
                icp.set_param(self._fields[name].config_parameter,
                              str(bool(record[name])))

    # -- test button ----------------------------------------------------------
    def action_test_verifier_connection(self):
        """Lightweight DNS-only probe to confirm base URL + key work."""
        self.ensure_one()
        # Persist current values first so the client reads them.
        self.set_values()
        result = self.env["email.verification.client"].verify_one(
            "postmaster@example.com", check_smtp=False, check_catch_all=False)
        status = (result or {}).get("status", "unknown")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Email Verifier reachable"),
                "message": _("Connection OK — test lookup returned status '%s'.") % status,
                "sticky": False,
            },
        }
