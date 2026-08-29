from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .outreach import WEEKDAY_NAMES


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    era_ai_autonomy_mode = fields.Selection(
        [("ramp", "Ramp — I approve every message"),
         ("full", "Full autonomy — send automatically")],
        string="Autonomy",
        default="ramp",
        config_parameter="era_ai_manager.autonomy_mode",
    )
    # Deliberately NOT config_parameter: res.config.settings refuses a Date
    # there (boolean/integer/float/char/selection/many2one/datetime only) and
    # raises while building the settings form — taking the whole Settings page
    # down, not just this block. A date picker is the right control for a
    # calendar date, so the parameter is read and written by hand below.
    era_ai_ramp_end_date = fields.Date(string="Switch to full autonomy on")
    era_ai_owner_email = fields.Char(
        string="Owner email",
        config_parameter="era_ai_manager.owner_email",
    )
    era_ai_mail_from = fields.Char(
        string="Send customer mail from",
        config_parameter="era_ai_manager.mail_from",
    )
    era_ai_cap_days = fields.Integer(
        string="Minimum days between marketing messages",
        default=7,
        config_parameter="era_ai_manager.cap_days",
    )
    era_ai_dedup_days = fields.Integer(
        string="Do not repeat the same play within (days)",
        default=30,
        config_parameter="era_ai_manager.dedup_days",
    )
    # These defaults must match the ones the module ships and its migration
    # sets. Odoo writes every config_parameter field on save, so a stale
    # default here silently reverts the real setting the moment anyone opens
    # this page and presses save — which is exactly how a 10:00-16:00 window
    # went back to 09:00-18:00 without anyone touching it.
    era_ai_send_hour_start = fields.Integer(
        string="Send window starts at",
        default=10,
        config_parameter="era_ai_manager.send_hour_start",
    )
    era_ai_send_hour_end = fields.Integer(
        string="Send window ends at",
        default=16,
        config_parameter="era_ai_manager.send_hour_end",
    )
    era_ai_send_days = fields.Char(
        string="Send on these days",
        default="sun,mon,tue,wed,thu",
        config_parameter="era_ai_manager.send_days",
        help="The working week for outreach, comma separated: sun, mon, tue, "
             "wed, thu, fri, sat. Replies to customers are never held back by "
             "this — answering someone who wrote to you is not marketing.",
    )
    era_ai_timezone = fields.Char(
        string="Send window timezone",
        default="Asia/Riyadh",
        config_parameter="era_ai_manager.timezone",
    )
    era_ai_pending_count = fields.Integer(
        string="Awaiting approval", compute="_compute_era_ai_counts"
    )
    era_ai_watchlist_count = fields.Integer(
        string="Watchlists", compute="_compute_era_ai_counts"
    )

    def get_values(self):
        res = super().get_values()
        stored = self.env["ir.config_parameter"].sudo().get_param(
            "era_ai_manager.ramp_end_date")
        res["era_ai_ramp_end_date"] = fields.Date.to_date(stored) if stored else False
        return res

    def set_values(self):
        self._check_send_days()
        super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            "era_ai_manager.ramp_end_date",
            fields.Date.to_string(self.era_ai_ramp_end_date)
            if self.era_ai_ramp_end_date else "",
        )

    def _check_send_days(self):
        """Reject a typo instead of quietly falling back to the default week.

        The reader of this field cannot tell "thurs" from "thu", and the
        engine treats anything it does not recognise as "use the default" —
        safe, but silent, and a silently ignored setting is worse than a
        refused one.
        """
        for settings in self:
            raw = (settings.era_ai_send_days or "").strip()
            if not raw:
                raise UserError(_(
                    "Name at least one day to send on, or outreach can never "
                    "go out. Use: sun, mon, tue, wed, thu, fri, sat."))
            unknown = [token.strip() for token in raw.split(",")
                       if token.strip()
                       and token.strip().lower()[:3] not in WEEKDAY_NAMES
                       and not (token.strip().isdigit()
                                and 0 <= int(token.strip()) <= 6)]
            if unknown:
                raise UserError(_(
                    "%(bad)s is not a day. Use: sun, mon, tue, wed, thu, fri, "
                    "sat.", bad=", ".join(unknown)))

    @api.depends_context("uid")
    def _compute_era_ai_counts(self):
        pending = self.env["era.ai.outreach"].search_count([("state", "=", "pending")])
        watchlists = self.env["era.ai.watchlist"].search_count([])
        for settings in self:
            settings.era_ai_pending_count = pending
            settings.era_ai_watchlist_count = watchlists

    # ------------------------------------------------------------------
    def action_era_ai_open_profile(self):
        """The business study: what the manager learned, and the brief it uses."""
        return self.env["era.ai.profile"].action_open_current()

    def action_era_ai_survey(self):
        """Collect the evidence now. Works with no AI configured at all."""
        profile = self.env["era.ai.profile"].current()
        profile.action_survey()
        return self.env["era.ai.profile"].action_open_current()

    def action_era_ai_open_persona(self):
        persona = self.env.ref(
            "era_ai_manager.persona_manager", raise_if_not_found=False
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("AI manager persona"),
            "res_model": "aidoo.persona",
            "view_mode": "form",
            "res_id": persona.id if persona else False,
        }

    def action_era_ai_open_agents(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("AI manager agents"),
            "res_model": "aidoo.scheduled",
            "view_mode": "list,form",
            "context": {"active_test": False},
        }

    def action_era_ai_run_watchdog(self):
        self.env["era.ai.watchdog.alert"]._cron_watchdog()
        return True
