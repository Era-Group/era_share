from . import models
from . import wizards
from . import controllers

# The endpoint every Era deployment shares. Installing the module should leave
# an instance able to index documents without anyone rediscovering these four
# values; see docs/LOCAL_EMBEDDINGS.md for why each one is what it is.
EMBEDDING_DEFAULTS = {
    "ai.custom_llm_embedding_base_url": "https://embed.era.net.sa/v1",
    "ai.custom_llm_embedding_model": "intfloat/multilingual-e5-large",
    # Route every agent's indexing here whatever its chat model: Odoo dedupes
    # embeddings by (checksum, embedding_model), so agents drifting onto
    # different embedding models multiplies the work and the storage.
    "ai.embedding_model_override": "custom_llm/text-embedding-3-small",
    # The endpoint is CPU-bound at well under a chunk per second. OpenAI's
    # inherited 2048 would be over an hour in one request, dying on a timeout
    # and leaving the source stuck in "processing" with nothing to explain it.
    # 16 measured at ~42s against Cloudflare's 100s ceiling; 32 came in at 84s,
    # which is a 16-second margin and not a margin at all. Indexing is once per
    # document, so the extra round trips cost nothing anyone will notice.
    "ai.custom_llm_embedding_batch_size": "16",
}


def _set_embedding_defaults(env):
    """Fill in the shared embeddings configuration, without ever overwriting.

    Only absent keys are written. An instance already pointed at OpenAI, or at
    an embeddings server of its own, must not have that silently redirected by
    installing or upgrading this module.

    The bearer token is deliberately NOT among these. It is a secret, it does
    not belong in a git repository, and it differs per deployment — Settings
    shows the endpoint as unreachable until someone enters it.
    """
    params = env["ir.config_parameter"].sudo()
    for key, value in EMBEDDING_DEFAULTS.items():
        if not params.get_param(key):
            params.set_param(key, value)
