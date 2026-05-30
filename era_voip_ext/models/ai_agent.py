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
        # Call super() — NOT models.Model.create — so the whole ai.agent MRO
        # runs: base ai.agent.create provisions the linked res.partner, and when
        # ai_crm is installed utm.source.mixin.create provisions the REQUIRED
        # source_id. Bypassing the chain left source_id NULL, which aborted any
        # install that creates an agent (NotNullViolation on ai_agent.source_id,
        # e.g. era_seo_suite's seeded SEO agent on a fresh DB that has ai_crm).
        ai_agents = super().create(vals_list)
        for agent in ai_agents:
            if not agent.image_128:
                agent.image_128 = base64.b64encode(image_placeholder)
        return ai_agents
