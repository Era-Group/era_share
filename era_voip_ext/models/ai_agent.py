import base64

from odoo import api, models
from odoo.tools import file_open

try:
    from odoo.addons.ai.utils.llm_providers import check_model_depreciation
except ImportError:
    check_model_depreciation = None


class AIAgent(models.Model):
    _inherit = "ai.agent"

    @api.model_create_multi
    def create(self, vals_list):
        with file_open("ai/static/description/icon.png", "rb") as f:
            image_placeholder = f.read()
        for vals in vals_list:
            if check_model_depreciation:
                check_model_depreciation(self.env, vals.get("llm_model"))
            if not vals.get("partner_id"):
                partner = self.env["res.partner"].create(
                    {
                        "name": vals.get("name"),
                        "active": False,
                    }
                )
                vals["partner_id"] = partner.id
        ai_agents = models.Model.create(self, vals_list)
        for agent in ai_agents:
            if not agent.image_128:
                agent.image_128 = base64.b64encode(image_placeholder)
        return ai_agents
