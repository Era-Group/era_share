from odoo.addons.ai.utils.llm_providers import (
    EMBEDDING_MODELS_SELECTION,
    PROVIDERS,
    Provider,
)


CUSTOM_LLM_MODEL_KEY = "custom_llm/custom"
CUSTOM_LLM_EMBEDDING_KEY = "custom_llm/text-embedding-3-small"


def _patch_providers():
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
