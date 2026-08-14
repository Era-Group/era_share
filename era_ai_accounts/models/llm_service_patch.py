"""Thin, account-aware layer over LLMApiService.

Applied AFTER ``custom_llm_service_patch`` (the absorbed era_odoo_ai_ext engine),
so the standard OpenAI/Google paths and the OpenAI-compatible custom_llm path are
preserved untouched. This layer only adds:

* binding of the request's ``era.ai.account`` (from ``env.context['era_ai_account_id']``);
* per-account credential resolution for api_key accounts (openai/google/anthropic/custom);
* the ``anthropic`` (Messages API), ``cloudflare`` and shared OpenAI-compatible
  (``zai`` / ``kimi``) HTTP transports;
* the ``anthropic_cli`` / ``openai_cli`` / ``kimi_cli`` transports (local Claude,
  Codex and Kimi Code CLIs).

Tool calling over the CLI transports
------------------------------------
Upstream's ``request_llm`` loop (``_request_llm_silent``) owns tool execution:
it repeatedly calls ``_request_llm`` and runs whatever ``next_actions`` it
returns, appending each result via ``_build_tool_call_response``. The CLI
transports plug into that loop with a text protocol: the available tools are
described in the system prompt, the model calls one by replying with a strict
JSON envelope (``{"tool_call": {"name": ..., "arguments": {...}}}``) which we
parse into ``next_actions``; tool results come back through ``inputs`` as
OpenAI-style ``function_call_output`` entries that ``_flatten`` renders into
the next round's prompt. Execution, permission checks, and the
``ai.max_successive_calls`` cap all stay upstream.

Everything else is delegated to the captured "original" methods.
"""
import json
import logging
import re
import uuid

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.ai.utils.llm_api_service import LLMApiService

from ..utils import codex_cli_transport
from ..utils import kimi_cli_transport
from ..utils import llm_cli_transport
from .custom_llm_service_patch import (
    _autofill_required_tool_arguments,
    _coerce_tool_arguments_for_schema,
    _extract_tool_calls_from_text,
)
from .era_ai_account import KIMI_OPENAI_BASE_URL, ZAI_OPENAI_BASE_URL

_logger = logging.getLogger(__name__)

_CLI_PROVIDERS = ("anthropic_cli", "openai_cli", "kimi_cli")

# Providers served by one shared OpenAI-compatible /chat/completions transport
# (native tool-calling included). Their only differences are the base URL and
# the model catalog, both resolved from the bound era.ai.account.
_OPENAI_COMPAT_PROVIDERS = ("zai", "kimi")

# Which utils module drives each CLI-proxy provider's binary. Z.AI is absent on
# purpose: it reuses the Claude binary (llm_cli_transport), redirected by env.
_CLI_TRANSPORTS = {
    "openai": codex_cli_transport,
    "kimi": kimi_cli_transport,
}

_ANTHROPIC_VERSION = "2023-06-01"


def _cfg(env, key, default=None):
    return env["ir.config_parameter"].sudo().get_param(key, default)


def _cfg_int(env, key, default):
    """Integer config parameter with a safe fallback on unset/garbage values."""
    try:
        return int(float(_cfg(env, key, default)))
    except (TypeError, ValueError):
        return int(default)


def _flatten(system_prompts, user_prompts, inputs):
    """Collapse Odoo's (system, user, inputs) into (system_with_history, last_user).

    Besides plain role/content messages, ``inputs`` may carry the OpenAI-style
    tool entries our CLI tool loop appends: ``function_call`` (the assistant's
    own call, kept so the model remembers what it asked for) and
    ``function_call_output`` (the executed result, fed back as the next user
    turn so the model can continue).
    """
    system_parts = [p for p in (system_prompts or []) if p]
    turns = []
    seq = list(inputs or []) + [{"role": "user", "content": p} for p in (user_prompts or [])]
    for item in seq:
        itype = item.get("type")
        if itype == "function_call":
            turns.append(("Assistant", "[tool call %s, id %s, arguments: %s]" % (
                item.get("name"), item.get("call_id"), item.get("arguments") or "{}")))
            continue
        if itype == "function_call_output":
            turns.append(("Tool", "Result of tool call %s:\n%s" % (
                item.get("call_id"), item.get("output") or "")))
            continue
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        turns.append(("User" if role == "user" else "Assistant", content or ""))
    last_user = ""
    ctx = turns
    if turns and turns[-1][0] in ("User", "Tool"):
        last_user = turns[-1][1]
        ctx = turns[:-1]
    elif turns:
        last_user = turns[-1][1]
    if ctx:
        system_parts.append("Conversation so far:\n" + "\n".join(f"{r}: {c}" for r, c in ctx))
    return "\n\n".join(system_parts), last_user


def _coerce_message_text(content):
    """Flatten an OpenAI-style ``message.content`` into a plain string.

    Cloudflare's OpenAI-compatible endpoint usually returns a string, but some
    models/gateways return a content-block list (``[{"type":"text","text":...}]``)
    or a single block dict — calling ``.strip()`` on those crashed live
    ('dict' object has no attribute 'strip'). Normalize all shapes to text.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text") or content.get("content") or ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def _openai_compat_build_messages(system_prompts, user_prompts, inputs):
    """Build OpenAI chat-completions ``messages`` from Odoo's (system, user, inputs).

    Besides plain role/content turns, ``inputs`` carries the OpenAI-style tool
    entries this module appends during the tool loop — ``function_call`` (the
    assistant's own call) and ``function_call_output`` (the executed result) —
    which must be rendered as a native ``assistant``/``tool`` message pair so
    the provider's chat-completions endpoint can continue the call. Consecutive
    ``function_call`` entries collapse into a single assistant message (parallel
    tool calls), as the chat-completions schema requires.

    Provider-agnostic: shared by every ``_OPENAI_COMPAT_PROVIDERS`` entry.
    """
    messages = []
    for prompt in (system_prompts or []):
        if prompt:
            messages.append({"role": "system", "content": prompt})
    for prompt in (user_prompts or []):
        if prompt:
            messages.append({"role": "user", "content": prompt})

    pending = []  # consecutive assistant tool_calls awaiting a flush

    def _flush():
        if pending:
            messages.append({"role": "assistant", "content": None, "tool_calls": list(pending)})
            pending.clear()

    for item in (inputs or []):
        itype = item.get("type")
        if itype == "function_call":
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments or {})
            pending.append({
                "id": item.get("call_id"),
                "type": "function",
                "function": {"name": item.get("name"), "arguments": arguments or "{}"},
            })
            continue
        _flush()
        if itype == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("call_id"),
                "content": str(item.get("output") or ""),
            })
            continue
        role = item.get("role", "user")
        if role not in ("user", "assistant", "system", "tool"):
            role = "user"
        messages.append({"role": role, "content": _coerce_message_text(item.get("content", ""))})
    _flush()

    # The endpoint needs at least one non-system turn to answer.
    if not any(m["role"] != "system" for m in messages):
        messages.append({"role": "user", "content": "Hello"})
    return messages


def _openai_compat_tools_payload(tools):
    """OpenAI chat-completions ``tools`` array from upstream's tools dict."""
    return [{
        "type": "function",
        "function": {"name": name, "description": description, "parameters": schema},
    } for name, (description, _allow_end, _fn, schema) in tools.items()]


# Historical names, kept so existing callers/tests keep importing them.
_zai_build_messages = _openai_compat_build_messages
_zai_tools_payload = _openai_compat_tools_payload


def _cli_tool_instructions(env, tools):
    """System-prompt block describing the available tools and the call protocol.

    ``tools`` is upstream's dict: {name: (description, allow_end_message,
    callable, parameter_json_schema)} — the schema already carries the
    ``__end_message`` property when early termination is allowed (the loop
    injects it before calling us).
    """
    max_rounds = _cfg_int(env, "ai.max_successive_calls", 20)
    lines = [
        "# Tool protocol — IMPORTANT",
        # The CLI's own agent persona believes it has no tools (its native ones
        # are disabled), so be explicit that Odoo executes these for it —
        # without this, the model answers "I can't access that tool" instead of
        # emitting the envelope (observed live on codex/gpt-5.4).
        "The calling system (Odoo) executes tools FOR you, outside this "
        "session. You request a tool by replying with a JSON envelope; Odoo "
        "runs it and sends you the result as the next message. This always "
        "works in this conversation — never claim you lack access to these "
        "tools, and never ask the user to enable them.",
        "To request tools, reply with ONLY this JSON object — no prose before "
        "or after, no code fences:",
        '{"tool_calls": [{"name": "<tool name>", "arguments": {<parameters per the schema>}}]}',
        "Rules:",
        "- Batch all independent calls of a step into the one tool_calls array.",
        "- Each result comes back as a 'Result of tool call <id>' message; then "
        "either request more tools the same way, or finish by writing the final "
        "answer as plain text (never as JSON).",
        "- Where a tool's schema has an __end_message parameter: if that call "
        "completes the user's request, put your final user-facing answer in "
        "__end_message — it ends the conversation immediately.",
        "- Never invent tool names. You have at most %d rounds; prefer few, "
        "well-batched calls." % max_rounds,
        "",
        "Example:",
        "User: how many products do we sell?",
        'You: {"tool_calls": [{"name": "count_products", "arguments": {}}]}',
        "(next message) Result of tool call clicall-abc123: 42",
        "You: We sell 42 products.",
        "",
        "Available tools:",
    ]
    for name, (description, _allow_end, _fn, schema) in tools.items():
        lines.append("- %s: %s\n  parameters (JSON Schema): %s"
                     % (name, description, json.dumps(schema)))
    return "\n".join(lines)


def _fill_null_array_arguments(arguments, tool_schema):
    """Missing/null array parameters become [] — mirroring what strict-mode
    OpenAI models send. Upstream fills omitted optional params with None and
    some tool implementations iterate them unguarded (observed live: Ask AI's
    read_group crashing on groupby=None, where groupby=[] is a valid
    'no grouping')."""
    if not isinstance(arguments, dict) or not isinstance(tool_schema, dict):
        return arguments
    for key, prop in (tool_schema.get("properties") or {}).items():
        declared = (prop or {}).get("type")
        types = declared if isinstance(declared, list) else [declared]
        if "array" in types and arguments.get(key) is None:
            arguments[key] = []
    return arguments


def _normalize_cli_tool_arguments(env, tools, tool_name, arguments):
    """Schema-normalize one parsed CLI tool call (same helpers as custom_llm)."""
    spec = tools.get(tool_name)
    if not spec:
        return arguments  # unknown tool: upstream replies with its error text
    schema = spec[3]
    arguments = _autofill_required_tool_arguments(arguments, schema, env.context)
    arguments = _coerce_tool_arguments_for_schema(arguments, schema)
    return _fill_null_array_arguments(arguments, schema)


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def _parse_cli_tool_calls(text):
    """Return [(tool_name, arguments_dict), ...] if the reply is a tool-call
    envelope, None for a plain answer, or "retry" for a malformed envelope
    (looks like a tool attempt but does not parse — worth one corrective round).

    Accepts the batched ``{"tool_calls": [...]}`` form we ask for plus the
    single ``{"tool_call": {...}}`` variant models sometimes produce, tolerates
    a fenced block, and salvages a leading JSON object from surrounding prose.
    """
    raw = (text or "").strip()
    fenced = _FENCE_RE.match(raw)
    if fenced:
        raw = fenced.group(1).strip()
    if '"tool_call' not in raw:  # covers both "tool_call" and "tool_calls"
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data, _end = json.JSONDecoder().raw_decode(raw[raw.index("{"):])
        except (ValueError, json.JSONDecodeError):
            _logger.warning(
                "era_ai_accounts: unparsable CLI tool-call envelope: %s", raw[:200])
            return "retry"
    if not isinstance(data, dict):
        return "retry"
    calls = data.get("tool_calls")
    if calls is None and isinstance(data.get("tool_call"), dict):
        calls = [data["tool_call"]]
    if not isinstance(calls, list):
        return "retry"
    parsed = []
    for call in calls:
        if isinstance(call, dict) and call.get("name"):
            arguments = call.get("arguments")
            parsed.append((str(call["name"]),
                           arguments if isinstance(arguments, dict) else {}))
    return parsed or "retry"


def _patch():
    if getattr(LLMApiService, "_era_ai_accounts_patched", False):
        return

    # Load-order guard: this layer captures custom_llm_service_patch's wrappers
    # as the "original" methods below and delegates the default branch to them.
    # If a future refactor reorders the imports in models/__init__.py so this
    # file loads first, it would capture the pristine upstream methods instead
    # — and every custom_llm request would bypass its model-fallback / retry
    # logic. Fail loudly at startup rather than degrade silently.
    if not getattr(LLMApiService, "_era_custom_llm_patched", False):
        raise ImportError(
            "era_ai_accounts: llm_service_patch must load AFTER "
            "custom_llm_service_patch (it captures that layer's wrappers as its "
            "originals). Check the import order in models/__init__.py."
        )

    original_init = LLMApiService.__init__
    original_get_api_token = LLMApiService._get_api_token
    original_get_base_headers = LLMApiService._get_base_headers
    original_request_llm = LLMApiService._request_llm
    original_build_tool_call_response = LLMApiService._build_tool_call_response

    def __init__(self, env, provider="openai"):
        account_id = env.context.get("era_ai_account_id")
        self._era_account = (
            env["era.ai.account"].sudo().browse(account_id) if account_id else None
        )
        if provider in _CLI_PROVIDERS:
            self.provider, self.env, self.base_url = provider, env, None
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
        if provider == "cloudflare" and self._era_account:
            # Cloudflare Workers AI exposes an OpenAI-compatible chat endpoint at
            # /accounts/<id>/ai/v1; the account id lives in the URL path.
            self.provider, self.env = "cloudflare", env
            self.base_url = self._era_account._cloudflare_base_url()
            return
        if provider in _OPENAI_COMPAT_PROVIDERS:
            # Z.AI (GLM) at api.z.ai/api/paas/v4, Kimi at api.moonshot.ai/v1 —
            # both plain OpenAI-compatible chat, so only the base URL differs.
            self.provider, self.env = provider, env
            default_base = ZAI_OPENAI_BASE_URL if provider == "zai" else KIMI_OPENAI_BASE_URL
            self.base_url = (
                (self._era_account and self._era_account.base_url) or default_base)
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
            # Strip CR/LF from the account-sourced header parts: requests rejects
            # them anyway, and this turns a sneaky header-injection attempt into a
            # plain (harmless) value instead of a crash.
            def _h(value):
                return (value or "").replace("\r", "").replace("\n", "").strip()

            header = _h(acc.auth_header) or "Authorization"
            prefix = _h(acc.auth_prefix) or "Bearer"
            token = _h(self._get_api_token())
            value = f"{prefix} {token}".strip() if prefix else token
            headers = {"Content-Type": "application/json", header: value}
            if acc.referer:
                headers["HTTP-Referer"] = _h(acc.referer)
            if acc.title:
                headers["X-Title"] = _h(acc.title)
            return headers
        if self.provider == "cloudflare" or self.provider in _OPENAI_COMPAT_PROVIDERS:
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._get_api_token()}",
            }
        return original_get_base_headers(self)

    def _request_llm(self, *args, **kwargs):
        if self.provider in _CLI_PROVIDERS:
            return _request_llm_cli(self, *args, **kwargs)
        if self.provider == "anthropic":
            return _request_llm_anthropic(self, *args, **kwargs)
        if self.provider == "cloudflare":
            return _request_llm_cloudflare(self, *args, **kwargs)
        if self.provider in _OPENAI_COMPAT_PROVIDERS:
            return _request_llm_openai_compat(self, *args, **kwargs)
        return original_request_llm(self, *args, **kwargs)

    def _build_tool_call_response(self, tool_call_id, return_value):
        # Upstream raises NotImplementedError for unknown providers; the CLI
        # tool loop and the OpenAI-compatible chat-completions loop reuse the
        # OpenAI function_call_output envelope (rendered back into the next
        # request).
        if self.provider in _CLI_PROVIDERS or self.provider in _OPENAI_COMPAT_PROVIDERS:
            return {
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": str(return_value),
            }
        return original_build_tool_call_response(self, tool_call_id, return_value)

    # ----------------------------------------------------------- new transports
    def _request_llm_cli(self, llm_model, system_prompts, user_prompts, tools=None,
                         files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False):
        acc = getattr(self, "_era_account", None)
        if not acc:
            raise UserError(_("No AI account bound to this CLI request."))
        acc._assert_usable()
        if tools and schema:
            # Same stance as upstream's Gemini branch (NotImplementedError).
            raise UserError(_(
                "The CLI proxy does not support structured output together with tools."))
        system_prompts = list(system_prompts or [])
        if tools:
            system_prompts.append(_cli_tool_instructions(self.env, tools))
        system_full, user_text = _flatten(system_prompts, user_prompts, inputs)
        # Guard against the tool-driven "Ask AI" navigation agent, whose system
        # context (full models/menus CSV) is hundreds of KB and makes the CLI/API
        # run out of memory. The CLI proxy is for chat/RAG agents only (v1).
        max_chars = _cfg_int(self.env, "ai.cli_max_prompt_chars", 400000)
        total = len(system_full) + len(user_text)
        if max_chars and total > max_chars:
            raise UserError(_(
                "This agent's prompt is too large for the local CLI proxy "
                "(%(n)s characters, limit %(max)s). This usually means a tool-driven "
                "'Ask AI' navigation agent — the CLI proxy supports chat/RAG only. "
                "Assign an API-key account to that agent, or point this "
                "account at a simpler chat agent. (Raise ai.cli_max_prompt_chars to override.)",
                n=total, max=max_chars))
        timeout = _cfg_int(self.env, "ai.cli_timeout", 180)
        transport = _CLI_TRANSPORTS.get(acc.provider, llm_cli_transport)
        text = transport.cli_complete(
            acc._cli_cfg(), llm_model, system_full, user_text, timeout=timeout)
        next_inputs = list(inputs or ())
        if tools:
            parsed = _parse_cli_tool_calls(text)
            if parsed == "retry":
                # Looked like a tool attempt but didn't parse: one corrective
                # round, then fall through (a JSON-ish final answer is still
                # masked by ai_agent._is_internal_tool_payload downstream).
                correction = (
                    "Your previous reply was not a valid tool-call envelope. "
                    "Reply again with exactly one JSON object of the form "
                    '{"tool_calls": [{"name": ..., "arguments": {...}}]} — or '
                    "answer the user in plain text."
                )
                text = transport.cli_complete(
                    acc._cli_cfg(), llm_model, system_full,
                    "%s\n\n%s" % (user_text, correction), timeout=timeout)
                parsed = _parse_cli_tool_calls(text)
            if isinstance(parsed, list):
                # Upstream's request_llm loop executes the tools and feeds each
                # function_call_output back into our next round via `inputs`.
                to_call = []
                for tool_name, arguments in parsed:
                    arguments = _normalize_cli_tool_arguments(
                        self.env, tools, tool_name, arguments)
                    call_id = "clicall-%s" % uuid.uuid4().hex[:12]
                    to_call.append((tool_name, call_id, arguments))
                    next_inputs.append({
                        "type": "function_call",
                        "name": tool_name,
                        "call_id": call_id,
                        "arguments": json.dumps(arguments),
                    })
                return [], to_call, next_inputs
        if not (text and text.strip()):
            raise UserError(_("The AI CLI returned an empty answer."))
        return [text], [], next_inputs

    def _request_llm_anthropic(self, llm_model, system_prompts, user_prompts, tools=None,
                               files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False):
        system_full, user_text = _flatten(system_prompts, user_prompts, inputs)
        max_tokens = _cfg_int(self.env, "ai.anthropic_max_tokens", 4096)
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

    def _request_llm_cloudflare(self, llm_model, system_prompts, user_prompts, tools=None,
                                files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False):
        # Cloudflare Workers AI, via its OpenAI-compatible /chat/completions endpoint.
        system_full, user_text = _flatten(system_prompts, user_prompts, inputs)
        messages = []
        if system_full:
            messages.append({"role": "system", "content": system_full})
        messages.append({"role": "user", "content": user_text or "Hello"})
        body = {"model": llm_model, "messages": messages}
        response = self._request(
            method="post", endpoint="/chat/completions",
            headers=self._get_base_headers(), body=body)
        choices = response.get("choices") or []
        message = choices[0].get("message") if choices else {}
        text = _coerce_message_text((message or {}).get("content"))
        if not text.strip():
            raise UserError(_("Cloudflare Workers AI returned no text."))
        return [text], [], list(inputs or ())

    def _request_llm_openai_compat(self, llm_model, system_prompts, user_prompts, tools=None,
                                   files=None, schema=None, temperature=0.2, inputs=(),
                                   web_grounding=False):
        # Z.AI (GLM) and Kimi (Moonshot) via their OpenAI-compatible
        # /chat/completions endpoints, with native tool-calling (both emit
        # standard OpenAI tool_calls). Plugs into upstream's request_llm loop
        # the same way the OpenAI path does: return (responses, to_call,
        # next_inputs); tool results come back through `inputs` as
        # function_call_output entries that _openai_compat_build_messages
        # renders into the next round's messages.
        messages = _openai_compat_build_messages(system_prompts, user_prompts, inputs)
        body = {"model": llm_model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = _openai_compat_tools_payload(tools)
            body["tool_choice"] = "auto"
        elif schema:
            # No tools: nudge strict JSON when the caller expects structured output.
            body["response_format"] = {"type": "json_object"}
        response = self._request(
            method="post", endpoint="/chat/completions",
            headers=self._get_base_headers(), body=body)
        choices = response.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}

        next_inputs = list(inputs or ())
        to_call = []
        for tc in (message.get("tool_calls") or []):
            function = tc.get("function") or {}
            name = function.get("name") or ""
            if not name:
                continue
            # Per the OpenAI spec `arguments` is a JSON string, but some
            # OpenAI-compatible gateways send an already-parsed dict — keep it
            # rather than feeding a dict to json.loads (TypeError -> dropped to
            # {}). Mirrors custom_llm_service_patch's tool-arg handling.
            raw_args = function.get("arguments")
            if isinstance(raw_args, dict):
                arguments = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    arguments = json.loads(raw_args)
                except json.JSONDecodeError:
                    arguments = {}
            else:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = tc.get("id") or ("zaicall-%s" % uuid.uuid4().hex[:12])
            arguments = _normalize_cli_tool_arguments(self.env, tools or {}, name, arguments)
            to_call.append((name, call_id, arguments))
            next_inputs.append({
                "type": "function_call", "name": name,
                "call_id": call_id, "arguments": json.dumps(arguments),
            })

        text = _coerce_message_text(message.get("content"))
        # Some models emit tool calls as plain-text JSON instead of the native
        # tool_calls field — salvage them (same helper as custom_llm).
        if tools and not to_call and text:
            for name, call_id, arguments in _extract_tool_calls_from_text(
                    text, tools, self.env.context):
                to_call.append((name, call_id, arguments))
                next_inputs.append({
                    "type": "function_call", "name": name,
                    "call_id": call_id, "arguments": json.dumps(arguments, ensure_ascii=False),
                })

        if to_call:
            return [], to_call, next_inputs
        if not (text and text.strip()):
            raise UserError(_("%(provider)s returned no text (finish_reason=%(reason)s).",
                              provider="Kimi" if self.provider == "kimi" else "Z.AI",
                              reason=(choices[0].get("finish_reason") if choices else None)))
        return [text], [], next_inputs

    LLMApiService.__init__ = __init__
    LLMApiService._get_api_token = _get_api_token
    LLMApiService._get_base_headers = _get_base_headers
    LLMApiService._request_llm = _request_llm
    LLMApiService._build_tool_call_response = _build_tool_call_response
    LLMApiService._request_llm_cli = _request_llm_cli
    LLMApiService._request_llm_anthropic = _request_llm_anthropic
    LLMApiService._request_llm_cloudflare = _request_llm_cloudflare
    LLMApiService._request_llm_openai_compat = _request_llm_openai_compat
    # Historical name, kept for callers that reference it directly.
    LLMApiService._request_llm_zai = _request_llm_openai_compat
    LLMApiService._era_ai_accounts_patched = True
    _logger.info(
        "era_ai_accounts: account-aware LLMApiService layer active "
        "(Claude/Codex/Kimi CLI + Anthropic + Z.AI + Kimi)")


_patch()
