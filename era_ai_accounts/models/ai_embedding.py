"""Make the ``custom_llm`` embedding option this module advertises actually writable.

``llm_providers_patch`` appends our provider to ``EMBEDDING_MODELS_SELECTION``,
which is the very list object ``ai`` passes to ``ai.embedding.embedding_model``.
That is enough for the option to *show* in the UI, but not to be *accepted*:
``fields.Selection.__init__`` snapshots the list into ``_selection`` at class
definition time, and ``convert_to_cache`` validates against that frozen copy.
Because ``ai`` is a dependency, its class body runs before we append — so every
write was refused with "Wrong value for ai.embedding.embedding_model", which in
turn left knowledge sources stuck in ``processing`` with no embeddings at all.

Re-sync the frozen copy once the registry is built.
"""
from odoo import models

from odoo.addons.ai.utils.llm_providers import EMBEDDING_MODELS_SELECTION


class AIEmbedding(models.Model):
    _inherit = "ai.embedding"

    def _register_hook(self):
        field = self._fields.get("embedding_model")
        frozen = getattr(field, "_selection", None)
        if isinstance(frozen, dict):
            for value, label in EMBEDDING_MODELS_SELECTION:
                frozen.setdefault(value, label)
        return super()._register_hook()
