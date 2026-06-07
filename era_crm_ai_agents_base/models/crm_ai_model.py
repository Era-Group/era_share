# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmAiModel(models.Model):
    """Catalog of available LLM models.

    One record per model the suite can call. The LLMRouter (task 0.3) picks a
    cheap vs advanced model by task sensitivity, then uses ``code`` as the
    provider's model identifier and ``env_key_param`` to look up the API key
    from ir.config_parameter / the environment — the secret itself is NEVER
    stored here, only the name of the config key that holds it (Rule 03 / PDPL).
    """

    _name = "crm.ai.model"
    _description = "CRM AI Model Catalog"
    _order = "provider, tier desc, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        string="Model Code",
        help="The provider's model identifier used in the API call "
             "(e.g. 'claude-opus-4-8', 'gpt-4o'). Used by the router.",
    )
    provider = fields.Selection(
        selection=[
            ("anthropic", "Anthropic"),
            ("openai", "OpenAI"),
            ("allam", "ALLaM"),
            ("local", "Local"),
        ],
        string="Provider",
        required=True,
    )
    tier = fields.Selection(
        selection=[
            ("cheap", "Cheap"),
            ("advanced", "Advanced"),
        ],
        string="Tier",
        required=True,
        default="cheap",
        help="Cheap models handle low-sensitivity tasks; advanced models are "
             "reserved for high-sensitivity ones. The router selects by tier.",
    )
    price_input_1k = fields.Float(
        string="Input Price / 1K",
        digits=(12, 6),
        help="Cost in USD per 1,000 input (prompt) tokens.",
    )
    price_output_1k = fields.Float(
        string="Output Price / 1K",
        digits=(12, 6),
        help="Cost in USD per 1,000 output (completion) tokens.",
    )
    max_context = fields.Integer(
        string="Max Context",
        help="Maximum context window size in tokens.",
    )
    env_key_param = fields.Char(
        string="API Key Parameter",
        help="The ir.config_parameter KEY NAME that holds this provider's API "
             "key (e.g. 'era_crm_ai_agents.anthropic_api_key'). NEVER store the "
             "secret value here — only the name of the parameter (Rule 03).",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "code_uniq",
            "unique(code)",
            "The model code must be unique.",
        ),
    ]

    @api.depends("name", "provider")
    def _compute_display_name(self):
        for record in self:
            if record.provider:
                provider_label = dict(
                    self._fields["provider"]._description_selection(self.env)
                ).get(record.provider, record.provider)
                record.display_name = f"{record.name} ({provider_label})"
            else:
                record.display_name = record.name or ""
