# -*- coding: utf-8 -*-
"""Multi-LLM router service.

The single entry point every agent uses to call an LLM. It selects a model by
task sensitivity, builds the provider request, calls the provider over HTTP,
and returns the text plus token counts and cost.

Security (Rule 03 / PDPL):
- API keys are read from the environment at call time via ir.config_parameter
  (with an OS-environment fallback). They are NEVER stored on the model, held in
  a module-level constant, logged, returned to the caller, or placed in an
  exception message.

Scope: this service does NOT enforce the cost cap or record usage — that is the
mixin's job (task 0.4). The router only selects, calls, and reports cost.
"""
import logging
import os

import requests

_logger = logging.getLogger(__name__)

# Default provider endpoints. Overridable per deployment via ir.config_parameter
# (keys below) so staging/prod or self-hosted gateways can be pointed elsewhere.
_DEFAULT_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
    # allam / local have no default — they MUST be configured per deployment.
}
_ENDPOINT_PARAMS = {
    "anthropic": "era_crm_ai_agents.anthropic_base_url",
    "openai": "era_crm_ai_agents.openai_base_url",
    "allam": "era_crm_ai_agents.allam_base_url",
    "local": "era_crm_ai_agents.local_base_url",
}
_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT_PARAM = "era_crm_ai_agents.llm_timeout"
_DEFAULT_TIMEOUT = 180  # seconds; matches the wider suite default


class LLMRouterError(Exception):
    """Raised for any router failure: missing config, missing key, network
    error, or a provider error response.

    Never carries a secret value — only safe context (provider, status code,
    parameter *names*).
    """


class LLMRouter:
    """Stateless-per-call router. Construct with an Odoo environment.

    Usage (from the mixin):
        result = LLMRouter(self.env).call(prompt, sensitivity="high")
    """

    def __init__(self, env):
        self.env = env

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def call(self, prompt, sensitivity="low", system=None, max_tokens=1024):
        """Call an LLM and return a normalized result dict.

        :param prompt: the user prompt (str).
        :param sensitivity: 'low' -> cheap model, 'high' -> advanced model.
        :param system: optional system prompt (str).
        :param max_tokens: max output tokens (int).
        :returns: {'text', 'input_tokens', 'output_tokens', 'cost', 'model'}
        :raises LLMRouterError: on any configuration, network, or provider error.
        """
        if not prompt:
            raise LLMRouterError("Cannot call an LLM with an empty prompt.")

        model = self._select_model(sensitivity)
        token = self._read_token(model.env_key_param)
        timeout = self._read_timeout()

        # Selection is safe to log (no secret); the token never is.
        _logger.info(
            "LLMRouter routing sensitivity=%s -> model=%s (%s, %s)",
            sensitivity, model.code, model.provider, model.tier,
        )

        if model.provider == "anthropic":
            text, in_tok, out_tok = self._call_anthropic(
                model, token, prompt, system, max_tokens, timeout
            )
        elif model.provider in ("openai", "allam", "local"):
            # ALLaM / local are assumed to expose an OpenAI-compatible chat API.
            text, in_tok, out_tok = self._call_openai_compatible(
                model, token, prompt, system, max_tokens, timeout
            )
        else:
            raise LLMRouterError("Unsupported provider: %s" % model.provider)

        cost = self._compute_cost(model, in_tok, out_tok)
        return {
            "text": text,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost": cost,
            "model": model.code,
        }

    # ------------------------------------------------------------------
    # Selection / config helpers
    # ------------------------------------------------------------------
    def _select_model(self, sensitivity):
        """Return a crm.ai.model for the requested sensitivity.

        'high' -> advanced; anything else -> cheap (cost-safe default, Rule 14).
        Within a tier, prefer Anthropic, then fall back to any active model.
        """
        tier = "advanced" if sensitivity == "high" else "cheap"
        Model = self.env["crm.ai.model"]
        domain = [("tier", "=", tier), ("active", "=", True)]
        model = Model.search(domain + [("provider", "=", "anthropic")], limit=1)
        if not model:
            model = Model.search(domain, limit=1)
        if not model:
            raise LLMRouterError("No active '%s' model configured in the catalog." % tier)
        return model

    def _read_token(self, env_key_param):
        """Read the provider API key at call time. Kept local — never stored,
        logged, or returned. Primary source is ir.config_parameter (per the
        task); falls back to an OS environment variable so the secret can live
        purely in the environment and never in the database (Rule 03).
        """
        if not env_key_param:
            raise LLMRouterError("This model has no API key parameter configured.")
        token = self.env["ir.config_parameter"].sudo().get_param(env_key_param)
        if not token:
            # OS env fallback: try the raw name and an UPPER_SNAKE normalization.
            token = os.environ.get(env_key_param) or os.environ.get(
                env_key_param.replace(".", "_").upper()
            )
        if not token:
            # Safe to name the parameter; its value does not exist / is empty.
            raise LLMRouterError(
                "No API key found for parameter '%s'." % env_key_param
            )
        return token

    def _read_timeout(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(_TIMEOUT_PARAM)
        try:
            return int(raw) if raw else _DEFAULT_TIMEOUT
        except (TypeError, ValueError):
            return _DEFAULT_TIMEOUT

    def _endpoint(self, provider):
        param = _ENDPOINT_PARAMS.get(provider)
        url = self.env["ir.config_parameter"].sudo().get_param(param) if param else None
        url = url or _DEFAULT_ENDPOINTS.get(provider)
        if not url:
            raise LLMRouterError(
                "No endpoint configured for provider '%s' (set '%s')."
                % (provider, param)
            )
        return url

    def _compute_cost(self, model, input_tokens, output_tokens):
        """Cost in USD from the catalog's per-1K pricing."""
        return (
            (input_tokens / 1000.0) * (model.price_input_1k or 0.0)
            + (output_tokens / 1000.0) * (model.price_output_1k or 0.0)
        )

    # ------------------------------------------------------------------
    # Provider calls
    # ------------------------------------------------------------------
    def _post(self, url, headers, payload, timeout, provider):
        """POST helper that funnels every transport/provider error into a clean,
        catchable LLMRouterError. Never includes auth headers in the message.
        """
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            # Connection refused, DNS failure, timeout, etc. The requests
            # exception text carries the URL but never our headers/token.
            raise LLMRouterError(
                "Network error calling provider '%s'." % provider
            ) from exc
        if response.status_code >= 400:
            raise LLMRouterError(
                "Provider '%s' returned HTTP %s." % (provider, response.status_code)
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMRouterError(
                "Provider '%s' returned a non-JSON response." % provider
            ) from exc

    def _call_anthropic(self, model, token, prompt, system, max_tokens, timeout):
        url = self._endpoint("anthropic")
        headers = {
            "x-api-key": token,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": model.code,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        data = self._post(url, headers, payload, timeout, "anthropic")
        # content is a list of blocks; concatenate the text blocks.
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {}) or {}
        return (
            text,
            int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0),
        )

    def _call_openai_compatible(self, model, token, prompt, system, max_tokens, timeout):
        url = self._endpoint(model.provider)
        headers = {
            "Authorization": "Bearer %s" % token,
            "content-type": "application/json",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model.code,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        data = self._post(url, headers, payload, timeout, model.provider)
        choices = data.get("choices") or []
        text = ""
        if choices:
            text = (choices[0].get("message") or {}).get("content") or ""
        usage = data.get("usage", {}) or {}
        return (
            text,
            int(usage.get("prompt_tokens", 0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
        )
