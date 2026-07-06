# -*- coding: utf-8 -*-
"""Manager-facing Campaign Agent settings (No-Hardcoded-Policy rule).

Every behavioral value is an ``ir.config_parameter`` under the
``era_crm_ai_agents_campaign.*`` namespace. Defaults are FAIL-CLOSED: the
agent OFF, human approval ON, the PDPL guard ON, conservative caps. Booleans
persist as explicit ``'True'``/``'False'`` strings (the suite-wide
first-OFF-lost fix); numbers persist as explicit strings; the approver
Many2many persists as a comma-separated id list (config params hold strings).

The fields render in the module's OWN Settings app block, gated to
``group_crm_ai_manager`` — see views/res_config_settings_views.xml.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PARAM_PREFIX = "era_crm_ai_agents_campaign."

# Weekday codes for the blackout-days setting (stored as a comma list).
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # -- Core ---------------------------------------------------------------
    campaign_agent_enabled = fields.Boolean(
        string="Enable Campaign Agent",
        default=False,
        help="Master switch. OFF (default) = the daily cron is a no-op and "
             "nothing is ever selected, drafted or sent.",
        config_parameter=PARAM_PREFIX + "enabled",
    )
    campaign_llm_transport = fields.Selection(
        selection=[
            ("token", "Direct token (native API, env-only key)"),
            ("era_ai_accounts", "Era AI Accounts (editor subscription)"),
        ],
        string="LLM Transport",
        default="era_ai_accounts",
        help="Which transport the agent's LLM calls take. Both run through "
             "the base egress seam (AI Compliance Guard). Default: 'Era AI "
             "Accounts' (editor subscription). It is runtime-optional: when "
             "selected but unavailable, every run FAILS CLOSED. The direct-"
             "token transport remains fully selectable.",
        config_parameter=PARAM_PREFIX + "llm_transport",
    )
    campaign_require_human_approval = fields.Boolean(
        string="Require Human Approval",
        default=True,
        help="ON (default): every campaign stops at 'Pending Approval' and "
             "waits for a configured approver. OFF: campaigns whose lines are "
             "all above the confidence threshold are handed off automatically.",
        config_parameter=PARAM_PREFIX + "require_human_approval",
    )
    campaign_approver_user_ids = fields.Many2many(
        comodel_name="res.users",
        relation="crm_ai_campaign_approver_settings_rel",
        string="Campaign Approvers",
        help="Users allowed to approve/reject campaigns. FAIL CLOSED: when "
             "approval is required and this list is empty, campaigns are "
             "marked 'failed' and nothing is sent.",
    )

    # -- PDPL master toggle ---------------------------------------------------
    campaign_pdpl_guard_enabled = fields.Boolean(
        string="PDPL Guard",
        default=True,
        help="ON (default): consent is verified before selection, the LLM "
             "receives minimized business attributes only (no name/email/"
             "phone), and PDPL decisions are audited. OFF: ALL PDPL guards "
             "are skipped INCLUDING consent — marketing is sent WITHOUT "
             "consent verification and full legal/compliance responsibility "
             "shifts to the operator (see the manager manual).",
        config_parameter=PARAM_PREFIX + "pdpl_guard_enabled",
    )

    # -- Sending & scheduling -------------------------------------------------
    campaign_daily_limit = fields.Integer(
        string="Daily Partner Limit",
        default=50,
        help="Maximum partners targeted per day across all campaigns.",
        config_parameter=PARAM_PREFIX + "daily_limit",
    )
    campaign_max_daily_campaigns = fields.Integer(
        string="Max Campaigns / Day",
        default=5,
        help="Maximum number of campaigns the agent may build per day.",
        config_parameter=PARAM_PREFIX + "max_daily_campaigns",
    )
    campaign_cooldown_days = fields.Integer(
        string="Per-Partner Cooldown (days)",
        default=30,
        help="A partner emailed by this agent is not targeted again for this "
             "many days.",
        config_parameter=PARAM_PREFIX + "cooldown_days",
    )
    campaign_monthly_frequency_cap = fields.Integer(
        string="Monthly Frequency Cap",
        default=2,
        help="Absolute maximum campaign emails per partner per calendar "
             "month, across ALL campaigns.",
        config_parameter=PARAM_PREFIX + "monthly_frequency_cap",
    )
    campaign_send_window_start = fields.Float(
        string="Send Window Start (hour)",
        default=9.0,
        help="Sends (and generation runs) are allowed from this hour, in the "
             "configured send timezone. Outside the window the run defers.",
        config_parameter=PARAM_PREFIX + "send_window_start",
    )
    campaign_send_window_end = fields.Float(
        string="Send Window End (hour)",
        default=17.0,
        help="Sends are allowed until this hour, in the configured send "
             "timezone.",
        config_parameter=PARAM_PREFIX + "send_window_end",
    )
    campaign_send_tz = fields.Char(
        string="Send Timezone",
        default="Asia/Riyadh",
        help="IANA timezone the send window and blackout days are evaluated "
             "in. Default Asia/Riyadh.",
        config_parameter=PARAM_PREFIX + "send_tz",
    )
    campaign_blackout_days = fields.Char(
        string="Blackout Days",
        default="fri,sat",
        help="Comma-separated weekdays on which the agent never runs or "
             "sends: mon,tue,wed,thu,fri,sat,sun. Default 'fri,sat' (KSA "
             "weekend). Empty = no blackout day.",
        config_parameter=PARAM_PREFIX + "blackout_days",
    )

    # -- Quality & safety -----------------------------------------------------
    campaign_confidence_threshold = fields.Float(
        string="Confidence Threshold",
        default=0.7,
        help="Lines whose LLM match confidence is below this value are "
             "FORCED into human review, regardless of the approval toggle.",
        config_parameter=PARAM_PREFIX + "confidence_threshold",
    )
    campaign_llm_daily_call_cap = fields.Integer(
        string="LLM Daily Call Cap",
        default=200,
        help="Maximum LLM calls per day. Reaching it halts further "
             "generation for the day (fail closed); remaining partners are "
             "skipped with a recorded reason.",
        config_parameter=PARAM_PREFIX + "llm_daily_call_cap",
    )

    campaign_email_from = fields.Char(
        string="Campaign Sender Address",
        help="From address for campaign emails (e.g. 'Era Marketing "
             "<marketing@era.net.sa>'). Empty = use the company's email, "
             "then the running user's. If none can be resolved the hand-off "
             "FAILS CLOSED — nothing is sent from an undefined address.",
        config_parameter=PARAM_PREFIX + "email_from",
    )

    # -- Personalization ------------------------------------------------------
    campaign_default_lang = fields.Selection(
        selection=[("ar", "Arabic"), ("en", "English")],
        string="Default Language",
        default="ar",
        help="Drafting language for partners whose own language is not set.",
        config_parameter=PARAM_PREFIX + "default_lang",
    )

    # ------------------------------------------------------------------
    @api.constrains("campaign_blackout_days")
    def _check_blackout_days(self):
        for rec in self:
            raw = (rec.campaign_blackout_days or "").strip()
            if not raw:
                continue
            bad = [d for d in (p.strip().lower() for p in raw.split(","))
                   if d and d not in WEEKDAYS]
            if bad:
                raise UserError(_(
                    "Invalid blackout day(s): %(bad)s. Use comma-separated "
                    "codes from: %(codes)s.",
                    bad=", ".join(bad), codes=", ".join(WEEKDAYS)))

    # ------------------------------------------------------------------
    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        raw = icp.get_param(PARAM_PREFIX + "approver_user_ids") or ""
        ids = [int(t) for t in raw.split(",") if t.strip().isdigit()]
        users = self.env["res.users"].browse(ids).exists()
        res["campaign_approver_user_ids"] = [(6, 0, users.ids)]
        return res

    def set_values(self):
        """Persist booleans/numbers as explicit strings (never a bare False —
        ``set_param(key, False)`` DELETES the row), the approver M2M as a
        comma id list, and keep the agent registry row's transport in step
        with the selected LLM transport (fail closed at save time when the
        era_ai_accounts transport is selected but unavailable)."""
        self._validate_transport()
        super().set_values()
        icp = self.env["ir.config_parameter"]
        for name, field in self._fields.items():
            param = getattr(field, "config_parameter", None)
            if not param or not param.startswith(PARAM_PREFIX):
                continue
            if field.type == "boolean":
                icp.set_param(param, "True" if self[name] else "False")
            elif field.type in ("integer", "float"):
                icp.set_param(param, str(self[name]))
        icp.set_param(
            PARAM_PREFIX + "approver_user_ids",
            ",".join(str(i) for i in self.campaign_approver_user_ids.ids)
            or " ",  # single space: keep the row alive when the list empties
        )
        self._sync_agent_transport()

    def _validate_transport(self):
        for rec in self:
            if (rec.campaign_llm_transport == "era_ai_accounts"
                    and "era.ai.account" not in self.env):
                raise UserError(_(
                    "The 'Era AI Accounts' transport is selected but the "
                    "era_ai_accounts module is not installed. Install it or "
                    "select the direct-token transport (fail closed)."))

    def _sync_agent_transport(self):
        """Point the pre-seeded agent registry row at the selected transport.

        Managers (the only users who can save these settings) have full CRUD
        on crm.ai.agent, so this is a plain write — no sudo. When the CLI
        transport has no usable account we leave the row untouched and let
        the engine fail closed at run time (the save already warned)."""
        agent = self.env["crm.ai.agent"]._get_or_create_agent(
            "era_campaign", {"name": "Campaign Agent"})
        if self.campaign_llm_transport == "era_ai_accounts":
            account = self.env["era.ai.account"].search([], limit=1) \
                if "era.ai.account" in self.env else None
            if account:
                agent.write({"transport": "cli",
                             "cli_account_id": account.id})
        elif agent.transport != "api":
            agent.write({"transport": "api", "cli_account_id": False})
