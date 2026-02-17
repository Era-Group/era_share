from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def _default_custom_llm_referer(self):
        return self.env["ir.config_parameter"].sudo().get_param("web.base.url", "https://localhost")

    custom_llm_key_enabled = fields.Boolean(
        string="Enable custom LLM provider",
        compute="_compute_custom_llm_key_enabled",
        readonly=False,
        groups="base.group_system",
    )
    custom_llm_provider_name = fields.Char(
        string="Provider Name",
        config_parameter="ai.custom_llm_provider_name",
        default="OpenRouter Free",
        readonly=False,
        groups="base.group_system",
        help="Display name for your custom provider (e.g. OpenRouter, Groq, Together).",
    )
    custom_llm_key = fields.Char(
        string="Provider API key",
        config_parameter="ai.custom_llm_key",
        readonly=False,
        groups="base.group_system",
        help="API key used to authenticate requests to your custom LLM provider.",
    )
    custom_llm_base_url = fields.Char(
        string="Provider Base URL",
        config_parameter="ai.custom_llm_base_url",
        default="https://openrouter.ai/api/v1",
        readonly=False,
        groups="base.group_system",
        help="Base API URL for your provider (OpenAI-compatible endpoint root).",
    )
    custom_llm_auth_header = fields.Char(
        string="Authorization Header",
        config_parameter="ai.custom_llm_auth_header",
        default="Authorization",
        readonly=False,
        groups="base.group_system",
        help="HTTP header name used for auth token (usually 'Authorization').",
    )
    custom_llm_auth_prefix = fields.Char(
        string="Authorization Prefix",
        config_parameter="ai.custom_llm_auth_prefix",
        default="Bearer",
        readonly=False,
        groups="base.group_system",
        help="Prefix before API key in auth header (e.g. Bearer). Leave empty for raw token.",
    )
    custom_llm_model = fields.Char(
        string="Chat Model 1",
        config_parameter="ai.custom_llm_model",
        default="openrouter/free",
        readonly=False,
        groups="base.group_system",
        help="Primary model for custom provider requests.",
    )
    custom_llm_model_2 = fields.Char(
        string="Chat Model 2",
        config_parameter="ai.custom_llm_model_2",
        readonly=False,
        groups="base.group_system",
        help="Second fallback model, used when Model 1 fails.",
    )
    custom_llm_model_3 = fields.Char(
        string="Chat Model 3",
        config_parameter="ai.custom_llm_model_3",
        readonly=False,
        groups="base.group_system",
        help="Third fallback model, used when Model 2 fails.",
    )
    custom_llm_model_4 = fields.Char(
        string="Chat Model 4",
        config_parameter="ai.custom_llm_model_4",
        readonly=False,
        groups="base.group_system",
        help="Fourth fallback model, used when Model 3 fails.",
    )
    custom_llm_embedding_model = fields.Char(
        string="Embedding Model",
        config_parameter="ai.custom_llm_embedding_model",
        default="openrouter/free",
        readonly=False,
        groups="base.group_system",
        help="Embedding model sent for RAG embeddings with this custom provider.",
    )
    custom_llm_referer = fields.Char(
        string="Referer",
        config_parameter="ai.custom_llm_referer",
        default=_default_custom_llm_referer,
        readonly=False,
        groups="base.group_system",
        help="Optional HTTP-Referer header. Defaults to system base URL (web.base.url).",
    )
    custom_llm_title = fields.Char(
        string="App Name",
        config_parameter="ai.custom_llm_title",
        default="Odoo AI",
        readonly=False,
        groups="base.group_system",
        help="Optional X-Title header.",
    )

    def _compute_custom_llm_key_enabled(self):
        for record in self:
            record.custom_llm_key_enabled = bool(record.custom_llm_key)
