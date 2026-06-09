# -*- coding: utf-8 -*-
"""Manager-facing toggles for the AI protection layers (Change 1).

All four layers default ON. Operational layers (cost cap, audit) toggle freely.
The two COMPLIANCE layers (PII redaction, consent check) enforce PDPL, so:
  * the fields are restricted to the AI **manager** group (not a casual flag),
  * disabling one (True -> False) writes a ``compliance_disabled`` audit row
    recording who/when, and
  * the guard additionally warning-logs on every call while a layer is off.

The env-only-key assertion (Rule 03) is intentionally NOT exposed here — it is
always enforced and never toggleable.
"""
from odoo import api, fields, models

_MANAGER = "era_crm_ai_agents_base.group_crm_ai_manager"

# Compliance params -> (settings field, human label) for the disable audit.
_COMPLIANCE = [
    ("era_crm_ai_agents.enable_pii_redaction", "era_enable_pii_redaction", "PII redaction"),
    ("era_crm_ai_agents.enable_consent_check", "era_enable_consent_check", "consent check"),
]


def _is_on(value):
    """A protection param counts as ON when set truthy OR unset (default ON)."""
    return value is None or str(value).strip().lower() in ("1", "true", "yes", "on")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Operational layers (free to toggle).
    era_enable_cost_cap = fields.Boolean(
        string="Enforce AI cost cap (Rule 14)", groups=_MANAGER,
        config_parameter="era_crm_ai_agents.enable_cost_cap")
    era_enable_audit = fields.Boolean(
        string="Detailed AI audit log (Rule 20)", groups=_MANAGER,
        config_parameter="era_crm_ai_agents.enable_audit")
    era_block_unpriced_model = fields.Boolean(
        string="Block unpriced AI models (Rule 14 fail-safe)", groups=_MANAGER,
        config_parameter="era_crm_ai_agents.block_unpriced_model")

    # Compliance layers (PDPL) — manager-only + audited on disable.
    era_enable_pii_redaction = fields.Boolean(
        string="PDPL: PII redaction", groups=_MANAGER,
        config_parameter="era_crm_ai_agents.enable_pii_redaction")
    era_enable_consent_check = fields.Boolean(
        string="PDPL: consent check", groups=_MANAGER,
        config_parameter="era_crm_ai_agents.enable_consent_check")

    def set_values(self):
        """Audit any compliance layer being turned OFF (True -> False)."""
        icp = self.env["ir.config_parameter"].sudo()
        disabled = []
        for param, fname, label in _COMPLIANCE:
            if _is_on(icp.get_param(param)) and not bool(self[fname]):
                disabled.append((param, label))
        res = super().set_values()
        for param, label in disabled:
            self.env["crm.ai.audit.log"].log(
                "compliance_disabled", None, None,
                {"param": param, "state": "enabled"},
                {"param": param, "state": "disabled",
                 "by_uid": self.env.uid, "label": label},
            )
        return res
