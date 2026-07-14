"""Ensure the OpenAI-compatible ``custom_llm`` provider is registered.

Absorbed from ``era_odoo_ai_ext``. This is idempotent and mirrors the canonical
definition also installed early by ``fix_ai_custom_llm_provider_preload`` (which
must keep running so full upgrades don't abort before this module is imported).

Account-routed requests do NOT rely on PROVIDERS — they build a LLMApiService
directly from the account's ``_service_provider()`` token — so Anthropic/CLI
transports are intentionally not added to the agent's static model Selection.
"""
from odoo.addons.ai.utils.llm_providers import (
    EMBEDDING_MODELS_SELECTION,
    PROVIDERS,
    Provider,
)

CUSTOM_LLM_MODEL_KEY = "custom_llm/custom"
CUSTOM_LLM_EMBEDDING_KEY = "custom_llm/text-embedding-3-small"


def _ensure_custom_llm_provider():
    if any(p.name == "custom_llm" for p in PROVIDERS):
        return
    vals = {
        "name": "custom_llm",
        "display_name": "Custom LLM",
        "embedding_model": CUSTOM_LLM_EMBEDDING_KEY,
        "embedding_config": {"max_batch_size": 2048, "max_tokens_per_request": 200000},
        "llms": [(CUSTOM_LLM_MODEL_KEY, "Custom LLM (Configured Model)")],
    }
    # Forward/backward compatible with core Provider signature changes (e.g.
    # the required ``deprecated_models`` field added in newer cores): give an
    # empty default to any Provider field we don't explicitly set, so a core
    # update never crashes the whole registry at import time again.
    for field in getattr(Provider, "_fields", ()):
        if field not in vals:
            vals[field] = []
    PROVIDERS.append(Provider(**vals))
    if (CUSTOM_LLM_EMBEDDING_KEY, "Custom LLM") not in EMBEDDING_MODELS_SELECTION:
        EMBEDDING_MODELS_SELECTION.append((CUSTOM_LLM_EMBEDDING_KEY, "Custom LLM"))


_ensure_custom_llm_provider()
