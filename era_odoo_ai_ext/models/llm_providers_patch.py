from odoo.addons.ai.utils.llm_providers import (
    EMBEDDING_MODELS_SELECTION,
    PROVIDERS,
    Provider,
)


CUSTOM_LLM_MODEL_KEY = "custom_llm/custom"
CUSTOM_LLM_EMBEDDING_KEY = "custom_llm/text-embedding-3-small"


def _patch_providers():
    # Surface OpenAI's gpt-5-nano in the LLM Model dropdown (idempotent). The
    # selection is built dynamically from PROVIDERS, so appending here is enough.
    # gpt-5-nano is a gpt-5-family reasoning model that rejects `temperature`;
    # that param is stripped for it in llm_api_service_patch (the base only
    # special-cases gpt-5 / gpt-5-mini).
    for provider in PROVIDERS:
        if provider.name == "openai" and not any(
                m[0] == "gpt-5-nano" for m in provider.llms):
            provider.llms.append(("gpt-5-nano", "GPT-5 Nano"))
            break

    if any(provider.name == "custom_llm" for provider in PROVIDERS):
        return

    PROVIDERS.append(
        Provider(
            name="custom_llm",
            display_name="Custom LLM",
            embedding_model=CUSTOM_LLM_EMBEDDING_KEY,
            embedding_config={
                "max_batch_size": 2048,
                "max_tokens_per_request": 200000,
            },
            llms=[
                (CUSTOM_LLM_MODEL_KEY, "Custom LLM (Configured Model)"),
            ],
        )
    )

    if (CUSTOM_LLM_EMBEDDING_KEY, "Custom LLM") not in EMBEDDING_MODELS_SELECTION:
        EMBEDDING_MODELS_SELECTION.append((CUSTOM_LLM_EMBEDDING_KEY, "Custom LLM"))


_patch_providers()
