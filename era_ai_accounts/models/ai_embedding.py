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

    def _create_batches(self, embeddings, provider):
        """Keep one HTTP request short enough to finish before it is cut off.

        ``custom_llm`` inherits OpenAI's ``max_batch_size`` of 2048, which is
        right for a hosted GPU and badly wrong for a CPU embedder: at the ~0.6
        chunks/s a small server manages, a full batch is over an hour in a
        single request. It would die on the HTTP timeout, or on a reverse
        proxy's, and the only visible symptom is a knowledge source stuck in
        "processing" — the failure mode this whole module exists to avoid.

        Split further for custom_llm when a cap is configured. Other providers
        are untouched.
        """
        batches = super()._create_batches(embeddings, provider)
        if provider != "custom_llm":
            return batches
        try:
            limit = int(self.env["ir.config_parameter"].sudo().get_param(
                "ai.custom_llm_embedding_batch_size", 0) or 0)
        except ValueError:
            return batches
        if limit <= 0:
            return batches
        capped = []
        for batch in batches:
            for start in range(0, len(batch), limit):
                capped.append(batch[start:start + limit])
        return capped

    def _register_hook(self):
        field = self._fields.get("embedding_model")
        frozen = getattr(field, "_selection", None)
        if isinstance(frozen, dict):
            for value, label in EMBEDDING_MODELS_SELECTION:
                frozen.setdefault(value, label)
        return super()._register_hook()
