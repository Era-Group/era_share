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
from odoo import _, api, fields, models
from odoo.exceptions import UserError

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

    # Agent cron execution identity (Rule 09). One identity for the whole suite;
    # every agent cron resolves it via crm.ai.agent._get_cron_run_user().
    era_cron_run_mode = fields.Selection(
        selection=[
            ("user", "Run as a chosen internal user (recommended)"),
            ("odoobot", "Run as OdooBot / superuser (bypasses access rules)"),
        ],
        string="Agent cron execution identity", groups=_MANAGER,
        config_parameter="era_crm_ai_agents.cron_run_mode",
        help="How scheduled agent runs authenticate. 'user' runs under real "
             "ACLs/record rules as the selected internal account (Rule 09). "
             "'OdooBot' runs as the superuser and bypasses all access rules.")
    era_cron_run_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Agent cron run user", groups=_MANAGER,
        domain="[('share', '=', False)]",
        config_parameter="era_crm_ai_agents.cron_run_user_id",
        help="The internal user every agent cron runs as in 'user' mode. "
             "Defaults to the least-privilege 'CRM AI Automation' account; "
             "point it at any existing internal user (e.g. an already-billable "
             "account) to add no extra user. Must be internal (not portal).")

    def set_values(self):
        """Audit compliance-layer disables; validate the cron-run identity."""
        # Validation: in 'user' mode the chosen account must be a usable
        # INTERNAL user (Rule 09 / reject portal/public).
        if self.era_cron_run_mode == "user":
            u = self.era_cron_run_user_id
            if not u:
                raise UserError(_(
                    "Select an internal user for the agent cron to run as, or "
                    "switch the execution identity to OdooBot."))
            if u.share:
                raise UserError(_(
                    "The agent cron run user must be an INTERNAL user. "
                    "'%(name)s' is a portal/public user.", name=u.name))
            if not u.active:
                raise UserError(_(
                    "The agent cron run user '%(name)s' is archived. Pick an "
                    "active internal user.", name=u.name))
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
