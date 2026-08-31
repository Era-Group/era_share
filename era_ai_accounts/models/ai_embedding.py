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
import logging

from odoo import models

from odoo.addons.ai.utils.llm_providers import EMBEDDING_MODELS_SELECTION

_logger = logging.getLogger(__name__)


class AIEmbedding(models.Model):
    _inherit = "ai.embedding"

    def _cron_generate_embedding(self, batch_size=100):
        """Re-open sources that are indexed in name only, then embed.

        Chunks are shared by ``(checksum, embedding_model)``: the first source
        needing a file creates them, every later copy is skipped as already
        done. So the rows live under exactly one attachment, and deleting that
        one source cascades them away — leaving every other copy of the same
        document still marked "indexed" while contributing nothing to
        retrieval. Nothing errors, nothing is logged, and the answer quietly
        stops citing that statute.

        Cheap to detect and cheap to repair, so do it on the way in.
        """
        self._reopen_sources_without_embeddings()
        self._clear_stale_failure_flags()
        result = super()._cron_generate_embedding(batch_size=batch_size)
        self._close_out_fully_embedded_sources()
        return result

    def _close_out_fully_embedded_sources(self):
        """Mark a source indexed when its chunks were embedded for a sibling.

        Core flips status and is_active while processing a batch. A source
        whose file was already chunked for another agent never has a batch of
        its own — the chunks are shared by ``(checksum, embedding_model)`` —
        so it sits in ``processing`` with every vector present.

        That is not cosmetic. ``is_active`` gates retrieval outright
        (``ai_embedding._get_similar_chunks`` filters on it), so the statute is
        fully indexed and contributes to nothing, with no error to notice.
        Two statutes were in that state after a corpus expansion.
        """
        pending = self.env["ai.agent.source"].sudo().search([
            ("status", "=", "processing"),
            ("attachment_id", "!=", False),
        ])
        closed = pending.browse()
        for source in pending:
            domain = [
                ("checksum", "=", source.attachment_id.checksum),
                ("embedding_model", "=", source.agent_id._get_embedding_model()),
            ]
            if not self.sudo().search_count(domain):
                continue  # nothing embedded yet; leave it to the cron
            if self.sudo().search_count(domain + [("embedding_vector", "=", False)]):
                continue  # still mid-flight
            closed |= source
        if closed:
            _logger.info(
                "Marking %s source(s) indexed: their chunks were embedded under "
                "a sibling attachment, so no batch of their own ever ran",
                len(closed))
            closed.write({"status": "indexed", "is_active": True,
                          "error_details": False})
        return closed

    def _clear_stale_failure_flags(self):
        """Let "put the source back to processing" actually mean retry.

        Core skips any chunk with has_embedding_generation_failed, and nothing
        clears that flag when a source is returned to ``processing`` — so a
        batch interrupted by, say, a container restart is written off forever.
        The cron then reports no work and no error while a statute sits half
        indexed, which is indistinguishable from being done.

        A flagged chunk under a source that is processing again is a
        contradiction; the source state is the newer intent, so honour it.
        """
        stale = self.sudo().search([
            ("embedding_vector", "=", False),
            ("has_embedding_generation_failed", "=", True),
            ("checksum", "in", self.env["ai.agent.source"].sudo().search([
                ("status", "=", "processing"),
            ]).mapped("attachment_id.checksum")),
        ])
        if stale:
            _logger.info("Clearing the failure flag on %s chunk(s) whose source "
                         "is being processed again", len(stale))
            stale.write({"has_embedding_generation_failed": False})
        return stale

    def _reopen_sources_without_embeddings(self):
        sources = self.env["ai.agent.source"].sudo().search([
            ("status", "=", "indexed"),
            ("attachment_id", "!=", False),
        ])
        orphaned = sources.browse()
        for source in sources:
            model = source.agent_id._get_embedding_model()
            if not self.sudo().search_count([
                ("checksum", "=", source.attachment_id.checksum),
                ("embedding_model", "=", model),
            ]):
                orphaned |= source
        if orphaned:
            _logger.info(
                "Re-indexing %s source(s) marked indexed with no embeddings",
                len(orphaned))
            orphaned.write({"status": "processing", "error_details": False})
        return orphaned

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
