# -*- coding: utf-8 -*-
"""Manager-facing compliance settings (No-Hardcoded-Policy rule).

Each field is bound to an ``ir.config_parameter`` under the
``era_crm_ai_agents_compliance.*`` namespace via ``config_parameter=``, so Odoo
reads/writes the param automatically. Defaults mirror
services/compliance_config.DEFAULTS and preserve the original KSA behavior.
The fields render on the global Settings page under the CRM AI Agents app
(manager-only block) — see views/res_config_settings_views.xml.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # -- Send-window: master + working hours --------------------------------
    crm_ai_send_window_enabled = fields.Boolean(
        string="Enforce send window", default=True,
        config_parameter="era_crm_ai_agents_compliance.send_window_enabled")
    crm_ai_working_hours_enabled = fields.Boolean(
        string="Block outside working hours", default=True,
        config_parameter="era_crm_ai_agents_compliance.working_hours_enabled")
    crm_ai_working_start = fields.Char(
        string="Working hours start (HH:MM)", default="09:00",
        config_parameter="era_crm_ai_agents_compliance.working_start")
    crm_ai_working_end = fields.Char(
        string="Working hours end (HH:MM)", default="21:00",
        config_parameter="era_crm_ai_agents_compliance.working_end")
    crm_ai_default_tz = fields.Char(
        string="Default timezone (fallback)", default="Asia/Riyadh",
        config_parameter="era_crm_ai_agents_compliance.default_tz")

    # -- Send-window: weekend ----------------------------------------------
    crm_ai_weekend_enabled = fields.Boolean(
        string="Block on weekend", default=True,
        config_parameter="era_crm_ai_agents_compliance.weekend_enabled")
    crm_ai_weekend_days = fields.Char(
        string="Weekend days", default="4,5",
        help="Comma-separated weekday numbers (Mon=0 … Sun=6). KSA = 4,5 (Fri,Sat).",
        config_parameter="era_crm_ai_agents_compliance.weekend_days")

    # -- Send-window: prayer / Jumu'ah / Ramadan ---------------------------
    crm_ai_prayer_enabled = fields.Boolean(
        string="Block during prayer times", default=True,
        config_parameter="era_crm_ai_agents_compliance.prayer_enabled")
    crm_ai_prayer_block_minutes = fields.Integer(
        string="Prayer block window (minutes)", default=30,
        config_parameter="era_crm_ai_agents_compliance.prayer_block_minutes")
    crm_ai_jumuah_enabled = fields.Boolean(
        string="Block Friday Jumu'ah", default=True,
        config_parameter="era_crm_ai_agents_compliance.jumuah_enabled")
    crm_ai_jumuah_start = fields.Char(
        string="Jumu'ah start (HH:MM)", default="11:30",
        config_parameter="era_crm_ai_agents_compliance.jumuah_start")
    crm_ai_jumuah_end = fields.Char(
        string="Jumu'ah end (HH:MM)", default="13:30",
        config_parameter="era_crm_ai_agents_compliance.jumuah_end")
    crm_ai_ramadan_enabled = fields.Boolean(
        string="Ramadan quiet hours", default=True,
        config_parameter="era_crm_ai_agents_compliance.ramadan_enabled")
    crm_ai_ramadan_start = fields.Char(
        string="Ramadan quiet start (HH:MM)", default="16:30",
        config_parameter="era_crm_ai_agents_compliance.ramadan_start")
    crm_ai_ramadan_end = fields.Char(
        string="Ramadan quiet end (HH:MM)", default="21:00",
        config_parameter="era_crm_ai_agents_compliance.ramadan_end")

    # -- Cultural norms -----------------------------------------------------
    crm_ai_norms_enabled = fields.Boolean(
        string="Enforce cultural norms", default=True,
        config_parameter="era_crm_ai_agents_compliance.norms_enabled")
    crm_ai_norms_check_greeting = fields.Boolean(
        string="Require greeting", default=True,
        config_parameter="era_crm_ai_agents_compliance.norms_check_greeting")
    crm_ai_norms_check_honorific = fields.Boolean(
        string="Require honorific", default=True,
        config_parameter="era_crm_ai_agents_compliance.norms_check_honorific")
    crm_ai_norms_check_tone = fields.Boolean(
        string="Check tone (shouting)", default=True,
        config_parameter="era_crm_ai_agents_compliance.norms_check_tone")

    # -- Consent / opt-out / DSAR ------------------------------------------
    crm_ai_required_consent_type = fields.Selection(
        selection=[("marketing", "Marketing"), ("service", "Service")],
        string="Required consent type for sends", default="marketing",
        config_parameter="era_crm_ai_agents_compliance.required_consent_type")
    crm_ai_opt_out_window_hours = fields.Integer(
        string="Opt-out enforcement window (hours)", default=72,
        config_parameter="era_crm_ai_agents_compliance.opt_out_window_hours")
    crm_ai_dsar_erasure_mode = fields.Selection(
        selection=[("anonymize", "Anonymize"), ("delete", "Hard delete")],
        string="DSAR erasure mode", default="anonymize",
        config_parameter="era_crm_ai_agents_compliance.dsar_erasure_mode")

    # -- Prayer-time source -------------------------------------------------
    crm_ai_prayer_source = fields.Selection(
        selection=[("api", "Live API (by city)"), ("fixed", "Fixed times")],
        string="Prayer-time source", default="api",
        config_parameter="era_crm_ai_agents_compliance.prayer_source")
    crm_ai_prayer_method = fields.Integer(
        string="Prayer calculation method", default=4,
        help="Aladhan method id. 4 = Umm al-Qura University, Makkah (KSA).",
        config_parameter="era_crm_ai_agents_compliance.prayer_method")
    crm_ai_default_city = fields.Char(
        string="Default city (fallback)", default="Riyadh",
        config_parameter="era_crm_ai_agents_compliance.default_city")
    crm_ai_default_country = fields.Char(
        string="Default country code (fallback)", default="SA",
        config_parameter="era_crm_ai_agents_compliance.default_country")
    crm_ai_prayer_fixed_times = fields.Char(
        string="Fixed prayer times (Fajr,Dhuhr,Asr,Maghrib,Isha)",
        default="05:00,12:00,15:30,18:15,19:45",
        config_parameter="era_crm_ai_agents_compliance.prayer_fixed_times")

    def set_values(self):
        """Persist our boolean/integer toggles as explicit STRINGS.

        ``ir.config_parameter.set_param`` UNLINKS a param when handed a Python
        ``False`` (or a falsy int via the bool path), so the stock
        ``res.config.settings`` flow would silently drop a toggle turned OFF and
        the engine would fall back to its default-ON value. Writing 'True' /
        'False' (and str(int)) keeps the value stored and unambiguous —
        byte-identical to compliance_config.DEFAULTS.
        """
        super().set_values()
        icp = self.env["ir.config_parameter"]
        for name, field in self._fields.items():
            param = getattr(field, "config_parameter", None)
            if not param or not param.startswith("era_crm_ai_agents_compliance."):
                continue
            if field.type == "boolean":
                icp.set_param(param, "True" if self[name] else "False")
            elif field.type == "integer":
                icp.set_param(param, str(self[name]))
