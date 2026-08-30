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
from odoo import models


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
