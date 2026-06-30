"""Thin, account-aware layer over LLMApiService.

Applied AFTER ``custom_llm_service_patch`` (the absorbed era_odoo_ai_ext engine),
so the standard OpenAI/Google paths and the OpenAI-compatible custom_llm path are
preserved untouched. This layer only adds:

* binding of the request's ``era.ai.account`` (from ``env.context['era_ai_account_id']``);
* per-account credential resolution for api_key accounts (openai/google/anthropic/custom);
* the ``anthropic`` (Messages API) and ``anthropic_cli`` (local CLI) transports.

Everything else is delegated to the captured "original" methods.
"""
import logging

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.ai.utils.llm_api_service import LLMApiService

from ..utils import llm_cli_transport

_logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"


def _cfg(env, key, default=None):
    return env["ir.config_parameter"].sudo().get_param(key, default)


def _flatten(system_prompts, user_prompts, inputs):
    """Collapse Odoo's (system, user, inputs) into (system_with_history, last_user)."""
    system_parts = [p for p in (system_prompts or []) if p]
    turns = []
    seq = list(inputs or []) + [{"role": "user", "content": p} for p in (user_prompts or [])]
    for item in seq:
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        turns.append(("User" if role == "user" else "Assistant", content or ""))
    last_user = ""
    ctx = turns
    if turns and turns[-1][0] == "User":
        last_user = turns[-1][1]
        ctx = turns[:-1]
    elif turns:
        last_user = turns[-1][1]
    if ctx:
        system_parts.append("Conversation so far:\n" + "\n".join(f"{r}: {c}" for r, c in ctx))
    return "\n\n".join(system_parts), last_user


def _patch():
    if getattr(LLMApiService, "_era_ai_accounts_patched", False):
        return

    original_init = LLMApiService.__init__
    original_get_api_token = LLMApiService._get_api_token
    original_get_base_headers = LLMApiService._get_base_headers
    original_request_llm = LLMApiService._request_llm

    def __init__(self, env, provider="openai"):
        account_id = env.context.get("era_ai_account_id")
        self._era_account = (
            env["era.ai.account"].sudo().browse(account_id) if account_id else None
        )
        if provider == "anthropic_cli":
            self.provider, self.env, self.base_url = "anthropic_cli", env, None
            return
        if provider == "anthropic":
            self.provider, self.env = "anthropic", env
            self.base_url = "https://api.anthropic.com/v1"
            return
        if provider == "custom_llm" and self._era_account:
            # Per-account custom endpoint (overrides the global ai.custom_llm_base_url).
            self.provider, self.env = "custom_llm", env
            self.base_url = self._era_account.base_url or _cfg(
                env, "ai.custom_llm_base_url", "https://openrouter.ai/api/v1")
            return
        return original_init(self, env, provider)

    def _get_api_token(self):
        acc = getattr(self, "_era_account", None)
        if acc and acc.auth_mode == "api_key":
            secret = acc._get_secret()
            if secret:
                return secret
            raise UserError(_("No API key set for AI account '%s'.", acc.name))
        return original_get_api_token(self)

    def _get_base_headers(self):
        acc = getattr(self, "_era_account", None)
        if self.provider == "anthropic":
            return {
                "content-type": "application/json",
                "x-api-key": self._get_api_token(),
                "anthropic-version": _ANTHROPIC_VERSION,
            }
        if self.provider == "custom_llm" and acc:
            header = acc.auth_header or "Authorization"
            prefix = acc.auth_prefix or "Bearer"
            token = self._get_api_token()
            value = f"{prefix} {token}".strip() if prefix else token
            headers = {"Content-Type": "application/json", header: value}
            if acc.referer:
                headers["HTTP-Referer"] = acc.referer
            if acc.title:
                headers["X-Title"] = acc.title
            return headers
        return original_get_base_headers(self)

    def _request_llm(self, *args, **kwargs):
        if self.provider == "anthropic_cli":
            return _request_llm_cli(self, *args, **kwargs)
        if self.provider == "anthropic":
            return _request_llm_anthropic(self, *args, **kwargs)
        return original_request_llm(self, *args, **kwargs)

    # ----------------------------------------------------------- new transports
    def _request_llm_cli(self, llm_model, system_prompts, user_prompts, tools=None,
                         files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False):
        acc = getattr(self, "_era_account", None)
        if not acc:
            raise UserError(_("No AI account bound to this CLI request."))
        acc._assert_usable()
        system_full, user_text = _flatten(system_prompts, user_prompts, inputs)
        # Guard against the tool-driven "Ask AI" navigation agent, whose system
        # context (full models/menus CSV) is hundreds of KB and makes the CLI/API
        # run out of memory. The CLI proxy is for chat/RAG agents only (v1).
        try:
            max_chars = int(_cfg(self.env, "ai.cli_max_prompt_chars", "400000"))
        except (TypeError, ValueError):
            max_chars = 400000
        total = len(system_full) + len(user_text)
        if max_chars and total > max_chars:
            raise UserError(_(
                "This agent's prompt is too large for the Claude CLI proxy "
                "(%(n)s characters, limit %(max)s). This usually means a tool-driven "
                "'Ask AI' navigation agent — the CLI proxy supports chat/RAG only. "
                "Assign an API-key Anthropic account to that agent, or point this "
                "account at a simpler chat agent. (Raise ai.cli_max_prompt_chars to override.)",
                n=total, max=max_chars))
        try:
            timeout = int(_cfg(self.env, "ai.cli_timeout", "180"))
        except (TypeError, ValueError):
            timeout = 180
        # Per-call tool opt-in via a generic context key. Defaults to "" so every
        # existing caller keeps the historical "no tools" behaviour untouched; a
        # caller that needs a built-in tool (e.g. WebSearch) sets
        # with_context(cli_allowed_tools="WebSearch") for that single run only.
        allowed_tools = self.env.context.get("cli_allowed_tools", "") or ""
        text = llm_cli_transport.cli_complete(
            acc._cli_cfg(), llm_model, system_full, user_text, timeout=timeout,
            allowed_tools=allowed_tools)
        if not (text and text.strip()):
            raise UserError(_("The AI CLI returned an empty answer."))
        return [text], [], list(inputs or ())

    def _request_llm_anthropic(self, llm_model, system_prompts, user_prompts, tools=None,
                               files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False):
        system_full, user_text = _flatten(system_prompts, user_prompts, inputs)
        try:
            max_tokens = int(_cfg(self.env, "ai.anthropic_max_tokens", "4096"))
        except (TypeError, ValueError):
            max_tokens = 4096
        body = {
            "model": llm_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_text or "Hello"}],
        }
        if system_full:
            body["system"] = system_full
        response = self._request(
            method="post", endpoint="/messages",
            headers=self._get_base_headers(), body=body)
        text = "".join(
            block.get("text", "")
            for block in (response.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            raise UserError(_("Anthropic returned no text (stop_reason=%s).", response.get("stop_reason")))
        return [text], [], list(inputs or ())

    LLMApiService.__init__ = __init__
    LLMApiService._get_api_token = _get_api_token
    LLMApiService._get_base_headers = _get_base_headers
    LLMApiService._request_llm = _request_llm
    LLMApiService._request_llm_cli = _request_llm_cli
    LLMApiService._request_llm_anthropic = _request_llm_anthropic
    LLMApiService._era_ai_accounts_patched = True
    _logger.info("era_ai_accounts: account-aware LLMApiService layer active (CLI + Anthropic)")


_patch()
