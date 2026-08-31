"""Custom LLM (OpenAI-compatible) provider settings, absorbed from era_odoo_ai_ext.

These global config parameters back the legacy, single custom_llm provider. They
remain available alongside the new per-account configuration.
"""
import json
import urllib.error
import urllib.request

from odoo import _, api, fields, models


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
    embedding_model_override = fields.Char(
        string="Embedding Routing", config_parameter="ai.embedding_model_override",
        readonly=False, groups="base.group_system",
        help="Forces every agent to index through this embedding model whatever "
             "its chat model. Odoo dedupes embeddings by (checksum, model), so "
             "agents drifting onto different models multiplies the work.",
    )
    custom_llm_embedding_key = fields.Char(
        string="Embedding API key", config_parameter="ai.custom_llm_embedding_key",
        readonly=False, groups="base.group_system",
        help="Bearer token for the embeddings endpoint. Separate from the chat "
             "key on purpose: the two are different services and must not be "
             "sent each other's secret. Left empty, the chat key is used.",
    )
    custom_llm_embedding_batch_size = fields.Char(
        string="Embedding Batch Size", config_parameter="ai.custom_llm_embedding_batch_size",
        readonly=False, groups="base.group_system",
        help="Chunks per request. A CPU-backed endpoint manages well under one "
             "per second, so a large batch runs past the HTTP timeout and the "
             "source is left stuck in 'processing'. Empty means no cap.",
    )
    custom_llm_embedding_base_url = fields.Char(
        string="Embedding Endpoint", config_parameter="ai.custom_llm_embedding_base_url",
        readonly=False, groups="base.group_system",
        help="Where embeddings are served. Normally a different service from "
             "the chat endpoint above, with its own key. Leave empty to use "
             "the chat endpoint for both.",
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
             "and users. 1 = strictly one at a time (recommended). Each CLI provider "
             "has its own slot pool of this size, except Codex and Kimi, which are "
             "always exactly 1: Codex's auth.json rotates on refresh, and Kimi's "
             "per-account config.toml/SYSTEM.md are rewritten before each call — "
             "both are single-writer.",
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

    # ----------------------------------------------------- embeddings
    # The CLI-proxy accounts are text-only, so knowledge sources cannot be
    # indexed through them. era_ai_accounts/tools/era_embed ships a small
    # OpenAI-compatible embeddings service; one instance serves every Odoo
    # host, and these fields are the Odoo half of that setup.
    embedding_service_status = fields.Char(
        string="Service status", compute="_compute_embedding_service_status",
        groups="base.group_system",
        help="Whether the embeddings endpoint is answering. Without it, "
             "knowledge sources stay in 'processing' forever with no error.",
    )

    def _probe_embedding_endpoint(self, base_url=None):
        """Return (reachable, detail) for the configured embeddings endpoint."""
        url = (base_url or self.DEFAULT_EMBED_URL).rstrip("/")
        url = url[:-3] if url.endswith("/v1") else url
        # Cloudflare answers 403 to urllib's default "Python-urllib/3.x" while
        # letting requests and curl through — so an unnamed probe reports the
        # endpoint dead while indexing, which goes through requests, is fine.
        probe = urllib.request.Request(
            f"{url}/health", headers={"User-Agent": "era-odoo-embeddings/1.0"})
        try:
            with urllib.request.urlopen(probe, timeout=5) as response:
                payload = json.loads(response.read())
            if payload.get("status") == "ok":
                return True, payload.get("model") or ""
            return False, _("unexpected reply: %s", payload)
        except (urllib.error.URLError, OSError, ValueError) as err:
            return False, str(err)

    @api.depends("custom_llm_embedding_base_url", "custom_llm_base_url")
    def _compute_embedding_service_status(self):
        for setting in self:
            reachable, detail = setting._probe_embedding_endpoint(
                setting.custom_llm_embedding_base_url or setting.custom_llm_base_url)
            setting.embedding_service_status = (
                _("Running — %s", detail) if reachable
                else _("Not reachable — %s", detail)
            )

    def action_restore_default_embedding_endpoint(self):
        """Fill the form with the shared defaults. Do not save them.

        A settings button that writes ir.config_parameter directly is wrong
        twice over: the change is committed without the user pressing Save, and
        the form still shows the old values because nothing told it otherwise —
        so it reads as a button that does nothing. Write to the transient
        record instead and return nothing: the client re-reads it, the fields
        visibly change, and Save remains the user's decision.

        The key is left alone. It is a secret that differs per deployment and
        this module has no business inventing one.
        """
        self.ensure_one()
        from odoo.addons.era_ai_accounts import EMBEDDING_DEFAULTS
        self.custom_llm_embedding_base_url = EMBEDDING_DEFAULTS[
            "ai.custom_llm_embedding_base_url"]
        self.custom_llm_embedding_model = EMBEDDING_DEFAULTS[
            "ai.custom_llm_embedding_model"]
        self.embedding_model_override = EMBEDDING_DEFAULTS[
            "ai.embedding_model_override"]
        self.custom_llm_embedding_batch_size = EMBEDDING_DEFAULTS[
            "ai.custom_llm_embedding_batch_size"]

    def action_test_embedding_endpoint(self):
        self.ensure_one()
        reachable, detail = self._probe_embedding_endpoint(
            self.custom_llm_embedding_base_url or self.custom_llm_base_url)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if reachable else "danger",
                "sticky": not reachable,
                "title": _("Embeddings service") if reachable else _("Not reachable"),
                "message": detail,
            },
        }
