"""Custom LLM (OpenAI-compatible) provider settings, absorbed from era_odoo_ai_ext.

These global config parameters back the legacy, single custom_llm provider. They
remain available alongside the new per-account configuration.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def _default_custom_llm_referer(self):
        return self.env["ir.config_parameter"].sudo().get_param("web.base.url", "https://localhost")

    custom_llm_key_enabled = fields.Boolean(
        string="Enable custom LLM provider",
        compute="_compute_custom_llm_key_enabled", readonly=False, groups="base.group_system",
    )
    custom_llm_provider_name = fields.Char(
        string="Provider Name", config_parameter="ai.custom_llm_provider_name",
        default="OpenRouter Free", readonly=False, groups="base.group_system",
    )
    custom_llm_key = fields.Char(
        string="Provider API key", config_parameter="ai.custom_llm_key",
        readonly=False, groups="base.group_system",
    )
    custom_llm_base_url = fields.Char(
        string="Provider Base URL", config_parameter="ai.custom_llm_base_url",
        default="https://openrouter.ai/api/v1", readonly=False, groups="base.group_system",
    )
    custom_llm_auth_header = fields.Char(
        string="Authorization Header", config_parameter="ai.custom_llm_auth_header",
        default="Authorization", readonly=False, groups="base.group_system",
    )
    custom_llm_auth_prefix = fields.Char(
        string="Authorization Prefix", config_parameter="ai.custom_llm_auth_prefix",
        default="Bearer", readonly=False, groups="base.group_system",
    )
    custom_llm_model = fields.Char(
        string="Chat Model 1", config_parameter="ai.custom_llm_model",
        default="openrouter/free", readonly=False, groups="base.group_system",
    )
    custom_llm_model_2 = fields.Char(
        string="Chat Model 2", config_parameter="ai.custom_llm_model_2",
        readonly=False, groups="base.group_system",
    )
    custom_llm_model_3 = fields.Char(
        string="Chat Model 3", config_parameter="ai.custom_llm_model_3",
        readonly=False, groups="base.group_system",
    )
    custom_llm_model_4 = fields.Char(
        string="Chat Model 4", config_parameter="ai.custom_llm_model_4",
        readonly=False, groups="base.group_system",
    )
    custom_llm_embedding_model = fields.Char(
        string="Embedding Model", config_parameter="ai.custom_llm_embedding_model",
        default="openrouter/free", readonly=False, groups="base.group_system",
    )
    custom_llm_referer = fields.Char(
        string="Referer", config_parameter="ai.custom_llm_referer",
        default=_default_custom_llm_referer, readonly=False, groups="base.group_system",
    )
    custom_llm_title = fields.Char(
        string="App Name", config_parameter="ai.custom_llm_title",
        default="Odoo AI", readonly=False, groups="base.group_system",
    )

    def _compute_custom_llm_key_enabled(self):
        for record in self:
            record.custom_llm_key_enabled = bool(record.custom_llm_key)
