from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _get_voip_config(self):
        config = super()._get_voip_config()
        params = self.env["ir.config_parameter"].sudo()
        enabled = params.get_param("voip_one_tab_enabled", "1")
        config["voip_one_tab_enabled"] = enabled not in ("0", "False", "false")
        return config
