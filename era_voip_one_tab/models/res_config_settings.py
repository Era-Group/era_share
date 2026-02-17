from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    voip_one_tab_enabled = fields.Boolean(
        string="VoIP single-tab guard",
        config_parameter="voip_one_tab_enabled",
        default=True,
        help=(
            "When enabled, SIP registers only when starting a call and any other open tab "
            "is disconnected first."
        ),
    )
    voip_one_tab_lock_timeout = fields.Integer(
        string="VoIP one-tab lock timeout (seconds)",
        config_parameter="voip_one_tab_lock_timeout",
        default=45,
        help=(
            "How long a tab lock remains valid without heartbeats. "
            "After this timeout, another tab can take over."
        ),
    )
