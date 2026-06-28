# -*- coding: utf-8 -*-
"""Manager-facing Enrichment settings (No-Hardcoded-Policy rule).

Every toggle is an ``ir.config_parameter`` under the
``era_crm_ai_agents_enrichment.*`` namespace. Conservative defaults: the whole
engine OFF, and WhatsApp presence OFF on its OWN separate toggle (Gate A). The
``set_values`` override writes booleans as explicit ``'True'``/``'False'``
strings so an OFF toggle persists (the first-OFF-lost fix).
"""
from odoo import fields, models

PARAM_PREFIX = "era_crm_ai_agents_enrichment."
_MANAGER = "era_crm_ai_agents_base.group_crm_ai_manager"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    enrichment_enabled = fields.Boolean(
        string="Enable Enrichment",
        default=False, groups=_MANAGER,
        config_parameter=PARAM_PREFIX + "enabled",
        help="Master switch for the whole enrichment engine. OFF = no provider "
             "is ever queried.")
    # Gate A for the WhatsApp-presence feature — SEPARATE from the master switch
    # and OFF by default. A number is transmitted only when this is ON AND
    # per-record consent (Gate B) positively passes AND the WAHA connector is
    # available.
    enrichment_whatsapp_check_enabled = fields.Boolean(
        string="Enable WhatsApp presence check",
        default=False, groups=_MANAGER,
        config_parameter=PARAM_PREFIX + "whatsapp_check_enabled",
        help="PDPL-sensitive: a presence check transmits an individual's mobile "
             "number to WhatsApp/Meta via the WAHA connector. OFF by default and "
             "separate from the master switch. Requires the WAHA connector to be "
             "available and per-record consent to pass.")
    enrichment_cache_ttl_days = fields.Integer(
        string="Contactability cache (days)",
        default=30, groups=_MANAGER,
        config_parameter=PARAM_PREFIX + "cache_ttl_days",
        help="Skip (and don't re-bill) re-checking the same email/number within "
             "this window.")
    enrichment_web_search_model = fields.Char(
        string="Web-search extraction model",
        default="gpt-4.1-mini", groups=_MANAGER,
        config_parameter=PARAM_PREFIX + "web_search_model",
        help="Native AI model used to extract structured data from web-search "
             "fallback results. Must exist in the Base rate card.")
    enrichment_cron_batch_size = fields.Integer(
        string="Scheduled batch size",
        default=50, groups=_MANAGER,
        config_parameter=PARAM_PREFIX + "cron_batch_size",
        help="How many stale partners the scheduled re-verification cron processes "
             "per run. The run still stops early if the cost cap trips.")
    enrichment_cron_staleness_days = fields.Integer(
        string="Staleness window (days)",
        default=30, groups=_MANAGER,
        config_parameter=PARAM_PREFIX + "cron_staleness_days",
        help="A deliverability check older than this many days is re-verified by "
             "the scheduled cron. Never-checked records are always eligible.")

    def set_values(self):
        """Persist booleans as explicit 'True'/'False' (the first-OFF-lost fix)."""
        super().set_values()
        icp = self.env["ir.config_parameter"]
        for name, field in self._fields.items():
            param = getattr(field, "config_parameter", None)
            if not param or not param.startswith(PARAM_PREFIX):
                continue
            if field.type == "boolean":
                icp.set_param(param, "True" if self[name] else "False")
            elif field.type == "integer":
                icp.set_param(param, str(self[name]))
