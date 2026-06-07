# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmAiModel(models.Model):
    """Catalog of available LLM models.

    One record per model the suite can call. The mixin picks a cheap vs advanced
    model by task sensitivity and passes ``code`` (the provider model id) and
    ``provider`` to Odoo's native AI service. Native AI supports OpenAI and Google
    only, so the catalog is constrained to those two providers.

    Secrets are never stored here. API keys come ONLY from the server environment
    via Odoo's native AI (env vars ODOO_AI_CHATGPT_TOKEN / ODOO_AI_GEMINI_TOKEN);
    the native UI key fields must be left blank and the guard fails closed if a
    key is found in the database (Rule 03 / PDPL). ``env_key_param`` is retained
    for documentation only.
    """

    _name = "crm.ai.model"
    _description = "CRM AI Model Catalog"
    _order = "provider, tier desc, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        string="Model Code",
        help="The provider's model identifier used in the native AI call "
             "(e.g. 'gpt-4o', 'gemini-2.5-flash'). Must match a model Odoo's "
             "native AI supports.",
    )
    provider = fields.Selection(
        selection=[
            ("openai", "OpenAI"),
            ("google", "Google"),
        ],
        string="Provider",
        required=True,
        help="Only OpenAI and Google are supported by Odoo 19 native AI.",
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
             "reserved for high-sensitivity ones. The mixin selects by tier.",
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
        string="API Key Env Var",
        help="Documentation only: the server ENV VAR Odoo native AI reads for "
             "this provider's key (ODOO_AI_CHATGPT_TOKEN for OpenAI, "
             "ODOO_AI_GEMINI_TOKEN for Google). NEVER store the secret here, and "
             "leave the native AI UI key fields blank (Rule 03).",
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
