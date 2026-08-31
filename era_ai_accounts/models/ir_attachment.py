"""Let non-Latin text documents reach the embedding pipeline.

``ir.attachment._index`` is a Python re-implementation of the unix ``strings``
command: for ``text/*`` it keeps only runs of printable **ASCII**
(``[\\x20-\\x7E]{4,}``). Arabic — like any non-Latin script — is multi-byte
UTF-8 with no byte in that range, so an Arabic text file indexes to the empty
string. ``ai`` then reads ``index_content`` in ``_get_attachment_content()``,
gets nothing, and marks the knowledge source "Invalid attachment. Failed to
extract content." The document is perfectly valid; only the ASCII filter is not.

Read the bytes ourselves when the ASCII pass came back empty. This is scoped to
the AI content extractor: ``index_content`` and attachment search are untouched,
so no reindexing of existing attachments is required.
"""
import logging

from odoo import api, models


_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    # Same guards as the core extractor, applied to our decoded text.
    _MIN_CONTENT_LENGTH = 10
    _MIN_UNIQUE_WORDS = 2

    def _get_attachment_content(self):
        if not (self.mimetype or "").startswith("text/"):
            return super()._get_attachment_content()
        # Tabular files are parsed row-by-row by core; only rescue them when
        # that parse came back empty, so CSV chunking keeps its shape.
        if self.mimetype in self.TABULAR_FILE_TYPES:
            return super()._get_attachment_content() or self._decoded_text_content()
        # A plain text file *is* its content — the ASCII pass can only lose
        # characters, and what it keeps of an Arabic document is punctuation and
        # digits, which is worse than nothing: it embeds noise instead of failing.
        return self._decoded_text_content() or super()._get_attachment_content()

    def _decoded_text_content(self):
        """The attachment decoded as UTF-8, or None if it is not usable text."""
        self.ensure_one()
        try:
            text = (self.raw or b"").decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        if len(text.strip()) <= self._MIN_CONTENT_LENGTH:
            return None
        if len({w.lower() for w in text.split()}) < self._MIN_UNIQUE_WORDS:
            return None
        return text


    @api.ondelete(at_uninstall=False)
    def _rehome_embeddings_before_unlink(self):
        """Hand a doomed attachment's chunks to a surviving twin.

        Chunks are created once per ``(checksum, embedding_model)`` and live
        under whichever attachment happened to be first; every other copy of
        the same file is skipped as already done. Deleting that one attachment
        therefore destroys the index for every source that shared it, while
        those sources go on reporting "indexed" — no error, no log, the statute
        simply stops being cited.

        Retrieval matches on ``ir_attachment.checksum``, not on the attachment
        id, so the rows are equally valid under any twin. Move them instead of
        losing them.
        """
        embeddings = self.env["ai.embedding"].sudo()
        doomed = self.filtered(lambda a: a.checksum)
        if not doomed:
            return
        held = embeddings.search([("attachment_id", "in", doomed.ids)])
        if not held:
            return
        for checksum, chunks in held.grouped(lambda e: e.attachment_id.checksum).items():
            survivor = self.sudo().search([
                ("checksum", "=", checksum),
                ("id", "not in", self.ids),
            ], limit=1)
            if not survivor:
                continue  # nothing to inherit them; let them go with the file
            chunks.write({"attachment_id": survivor.id})
            _logger.info(
                "Moved %s embedding chunk(s) to attachment %s so deleting %s "
                "does not unindex the copies that share its content",
                len(chunks), survivor.id, checksum[:12])
