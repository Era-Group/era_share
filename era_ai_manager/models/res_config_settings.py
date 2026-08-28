from odoo import _, api, fields, models


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
    era_ai_send_hour_start = fields.Integer(
        string="Send window starts at",
        default=9,
        config_parameter="era_ai_manager.send_hour_start",
    )
    era_ai_send_hour_end = fields.Integer(
        string="Send window ends at",
        default=18,
        config_parameter="era_ai_manager.send_hour_end",
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
        super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            "era_ai_manager.ramp_end_date",
            fields.Date.to_string(self.era_ai_ramp_end_date)
            if self.era_ai_ramp_end_date else "",
        )

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
