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

    # --- AI CLI rate protection (global, host-wide; applies per provider pool) ---
    # NB: this is a Boolean that defaults to ON. A plain `config_parameter`
    # Boolean cannot persist an unchecked (False) value — Odoo's set_param deletes
    # the key for a Python False, and its settings reader does bool("False") (==True)
    # — so it always reverts to the default on reload. We therefore store an
    # explicit "True"/"False" string via compute/inverse and parse it ourselves.
    cli_gap_enabled = fields.Boolean(
        string="Pace calls (gap between requests)",
        compute="_compute_cli_gap_enabled", inverse="_inverse_cli_gap_enabled",
        readonly=False, groups="base.group_system",
        help="Insert a delay between consecutive AI CLI calls so the connected "
             "account is not hit by rapid-fire requests. Turn off to disable the gap "
             "(the one-at-a-time limit still applies unless you raise concurrency).",
    )
    cli_min_gap = fields.Float(
        string="Base gap (seconds)", config_parameter="ai.cli_min_gap", default=1.0,
        readonly=False, groups="base.group_system",
        help="Minimum delay applied before every CLI call.",
    )
    cli_gap_per_kb = fields.Float(
        string="Extra gap per KB (seconds)", config_parameter="ai.cli_gap_per_kb", default=0.05,
        readonly=False, groups="base.group_system",
        help="Added to the gap for each KB of request body — bigger requests wait longer.",
    )
    cli_max_gap = fields.Float(
        string="Maximum gap (seconds)", config_parameter="ai.cli_max_gap", default=30.0,
        readonly=False, groups="base.group_system",
        help="Upper bound on the inter-call gap.",
    )
    cli_max_concurrency = fields.Integer(
        string="Max concurrent calls", config_parameter="ai.cli_max_concurrency", default=1,
        readonly=False, groups="base.group_system",
        help="How many Claude CLI calls may run at the same time across ALL workers "
             "and users. 1 = strictly one at a time (recommended). The Codex pool "
             "is always exactly 1, regardless of this setting — its auth.json is "
             "single-writer (tokens rotate on refresh).",
    )
    cli_lock_wait = fields.Integer(
        string="Max wait for a free slot (seconds)", config_parameter="ai.cli_lock_wait", default=300,
        readonly=False, groups="base.group_system",
        help="How long a request waits for a free slot before returning a 'busy' error.",
    )
    cli_timeout = fields.Integer(
        string="Call timeout (seconds)", config_parameter="ai.cli_timeout", default=180,
        readonly=False, groups="base.group_system",
        help="Maximum duration of a single AI CLI call.",
    )
    cli_max_prompt_chars = fields.Integer(
        string="Max prompt size (characters)", config_parameter="ai.cli_max_prompt_chars", default=400000,
        readonly=False, groups="base.group_system",
        help="Reject CLI-proxy requests whose prompt exceeds this size with a clear message "
             "(very large tool-driven 'Ask AI' contexts should use an API-key account).",
    )

    def _compute_custom_llm_key_enabled(self):
        for record in self:
            record.custom_llm_key_enabled = bool(record.custom_llm_key)

    def _compute_cli_gap_enabled(self):
        val = self.env["ir.config_parameter"].sudo().get_param("ai.cli_gap_enabled", "True")
        enabled = str(val).strip().lower() not in ("false", "0", "no", "off", "")
        for record in self:
            record.cli_gap_enabled = enabled

    def _inverse_cli_gap_enabled(self):
        icp = self.env["ir.config_parameter"].sudo()
        for record in self:
            icp.set_param("ai.cli_gap_enabled", "True" if record.cli_gap_enabled else "False")
