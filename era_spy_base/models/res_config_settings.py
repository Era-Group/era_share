# -*- coding: utf-8 -*-
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    eraspy_base_url = fields.Char(
        string="Era Enrich Base URL",
        default="https://spy.era.net.sa/api",
        config_parameter="era_spy.base_url",
        help="Root URL for the Era Enrich API or your proxy endpoint.",
    )
    eraspy_rate_limit_per_minute = fields.Integer(
        string="Era Enrich Max Calls / Minute",
        default=600,
        config_parameter="era_spy.rate_limit_per_minute",
        help="Person API limit is 600 elements/min. Reduce if you see 429 responses.",
    )
    eraspy_without_contacts_default = fields.Boolean(
        string="Default: Profile Preview Only",
        default=False,
        config_parameter="era_spy.without_contacts_default",
        help="If enabled, wizards fetch profiles without revealing contact details to save credits.",
    )
    eraspy_auto_enrich_enabled = fields.Boolean(
        string="Auto Enrich New Records",
        default=True,
        config_parameter="era_spy.auto_enrich_enabled",
        help="If enabled, newly created CRM leads and applicants are auto-enriched after one minute.",
    )

    def _param_to_bool(self, value, default=False):
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        value = str(value).strip().lower()
        return value in ("1", "true", "t", "yes", "y", "on")

    def get_values(self):
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        res.update(
            {
                "eraspy_without_contacts_default": self._param_to_bool(
                    ICP.get_param("era_spy.without_contacts_default"), default=False
                ),
                "eraspy_auto_enrich_enabled": self._param_to_bool(
                    ICP.get_param("era_spy.auto_enrich_enabled"), default=True
                ),
            }
        )
        return res

    def set_values(self):
        super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(
            "era_spy.without_contacts_default",
            "True" if self.eraspy_without_contacts_default else "False",
        )
        ICP.set_param(
            "era_spy.auto_enrich_enabled",
            "True" if self.eraspy_auto_enrich_enabled else "False",
        )
        if self.eraspy_rate_limit_per_minute and self.eraspy_rate_limit_per_minute < 1:
            _logger.warning("EraSpy rate limit set below 1; resetting to 1 to avoid division errors.")
            self.eraspy_rate_limit_per_minute = 1
