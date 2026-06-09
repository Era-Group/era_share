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
    PROVIDERS.append(
        Provider(
            name="custom_llm",
            display_name="Custom LLM",
            embedding_model=CUSTOM_LLM_EMBEDDING_KEY,
            embedding_config={"max_batch_size": 2048, "max_tokens_per_request": 200000},
            llms=[(CUSTOM_LLM_MODEL_KEY, "Custom LLM (Configured Model)")],
        )
    )
    if (CUSTOM_LLM_EMBEDDING_KEY, "Custom LLM") not in EMBEDDING_MODELS_SELECTION:
        EMBEDDING_MODELS_SELECTION.append((CUSTOM_LLM_EMBEDDING_KEY, "Custom LLM"))


_ensure_custom_llm_provider()
