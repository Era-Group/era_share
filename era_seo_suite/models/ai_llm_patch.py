"""Surface OpenAI's ``gpt-5-nano`` as a first-class model.

Background: ``gpt-5-nano`` was originally added by ``era_odoo_ai_ext``, but that
module depends on ``ai_app`` which is not installed here — so it got
auto-uninstalled and its patch no longer loads. era_seo_suite only depends on
``ai`` (always present), so we surface the model from here instead.

Why it matters: the blog generator's agent has ``llm_model = 'gpt-5-nano'``.
``ai.utils.llm_providers.get_provider()`` resolves the provider by scanning
``PROVIDERS``; if ``gpt-5-nano`` isn't there it raises *"No provider found for
the selected model"* and generation produces no article. Adding it under the
OpenAI provider makes it resolve to OpenAI (which authenticates with the
configured ``ai.openai_key``) and lists it in the LLM-model dropdown — both the
selection and ``get_provider`` read ``PROVIDERS`` dynamically.
"""
import logging

from odoo.addons.ai.utils import llm_api_service as _api
from odoo.addons.ai.utils import llm_providers as _providers

_logger = logging.getLogger(__name__)


# OpenAI models missing from Odoo's base provider list. gpt-4o-mini is a fast,
# cheap, NON-reasoning model — the recommended default for bulk blog generation
# (the base list ships gpt-4.1-mini but not gpt-4o-mini). gpt-5-nano is the
# small reasoning model.
_EXTRA_OPENAI_MODELS = [
    ("gpt-4o-mini", "GPT-4o Mini"),
    ("gpt-5-nano", "GPT-5 Nano"),
]


def _register_extra_models():
    for provider in _providers.PROVIDERS:
        if provider.name == "openai":
            for code, label in _EXTRA_OPENAI_MODELS:
                if not any(m[0] == code for m in provider.llms):
                    provider.llms.append((code, label))
                    _logger.info("era_seo_suite: registered %s under the "
                                 "OpenAI provider", code)
            return
    _logger.warning("era_seo_suite: OpenAI provider not found; could not "
                    "register extra models")


_register_extra_models()


# gpt-5-family models are reasoning models that REJECT the `temperature` param.
# The base only special-cases gpt-5 / gpt-5-mini when building the request body,
# so gpt-5-nano would still carry `temperature` and the OpenAI API would 400.
# Wrap the helper to strip it for any gpt-5* right before the request is sent.
# Guarded so re-imports don't wrap twice.
_LLMApiService = _api.LLMApiService
if not getattr(_LLMApiService._request_llm_openai_helper,
               "_era_seo_gpt5_temp_patch", False):
    _orig_openai_helper = _LLMApiService._request_llm_openai_helper

    def _request_llm_openai_helper(self, body, tools=None, inputs=()):
        if isinstance(body, dict) and (body.get("model") or "").startswith("gpt-5"):
            body.pop("temperature", None)
        return _orig_openai_helper(self, body, tools=tools, inputs=inputs)

    _request_llm_openai_helper._era_seo_gpt5_temp_patch = True
    _LLMApiService._request_llm_openai_helper = _request_llm_openai_helper
