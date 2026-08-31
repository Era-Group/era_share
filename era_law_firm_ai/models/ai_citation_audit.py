"""Check what the answer actually cited, instead of trusting it to behave.

Two of the module's promises were prompt paragraphs and nothing more. The
statute text carries «[ملغي]» on every article of a repealed law, and the
research agent is told to answer only from its sources — but a paragraph is
an instruction, not a control, and the failure it guards against is invisible:
a repealed article looks exactly like a current one once it is quoted, and an
answer with no citation reads no differently from a grounded one.

Both are mechanically checkable after the fact. Citations are emitted in a
fixed [SOURCE:<attachment id>] form, the attachments map to moj.law rows, and
those rows carry the statute's status. So the answer is inspected, and what is
found is appended by the system — the way the disclaimer is — rather than left
to the model to disclose.
"""
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Odoo's RAG prompt asks for exactly this shape; several ids may share one tag.
CITATION = re.compile(r'\[SOURCE:\s*([0-9,\s]+)\]')

CURRENT = 'ساري'


class LegalAIRequestCitations(models.Model):
    _inherit = 'legal.ai.request'

    cited_repealed = fields.Boolean(
        string='Cited a Repealed Statute', readonly=True, copy=False,
        help="The answer relied on a statute that is no longer in force. The text "
             "says so on every article, but a quotation carries no such mark.")
    cited_nothing = fields.Boolean(
        string='Answered Without Citing', readonly=True, copy=False,
        help="A source-restricted agent answered without citing any source. Its "
             "instruction to answer only from the attached texts is a prompt, "
             "not a gate, so this is how the gap becomes visible.")

    @api.model
    def _cited_attachment_ids(self, text):
        ids = set()
        for group in CITATION.findall(text or ''):
            for token in group.split(','):
                token = token.strip()
                if token.isdigit():
                    ids.add(int(token))
        return ids

    def _repealed_statutes_cited(self, text):
        """The statutes behind these citations that are no longer in force."""
        self.ensure_one()
        attachment_ids = self._cited_attachment_ids(text)
        if not attachment_ids:
            return self.env['moj.law']
        checksums = self.env['ir.attachment'].sudo().browse(
            sorted(attachment_ids)).exists().mapped('checksum')
        if not checksums:
            return self.env['moj.law']
        laws = self.env['moj.law'].sudo().search([
            ('source_ids.attachment_id.checksum', 'in', checksums),
        ])
        return laws.filtered(lambda law: (law.status or '') != CURRENT)

    def _store_sanitized_response(self, response):
        """Inspect the answer on the way in, and say what it relied on."""
        self.ensure_one()
        text = response or ''
        repealed = self._repealed_statutes_cited(text)
        uncited = bool(
            self.agent_id.restrict_to_sources and text.strip()
            and not self._cited_attachment_ids(text))

        notes = []
        if repealed:
            names = '، '.join(sorted(repealed.mapped('name')))
            notes.append(_(
                "⛔ تنبيه النظام: استند هذا الجواب إلى نظام غير سارٍ (%(names)s). "
                "راجع النص الساري قبل الاعتماد عليه.", names=names))
            _logger.warning(
                'legal.ai.request %s cited a repealed statute: %s', self.id, names)
        if uncited:
            notes.append(_(
                "⚠️ تنبيه النظام: هذا الوكيل مقيَّد بالمصادر المرفقة، ولم يستشهد "
                "الجواب بأي مصدر. عامله كرأي غير مُسنَد."))
            _logger.warning(
                'legal.ai.request %s: a source-restricted agent answered with no '
                'citation', self.id)

        if notes:
            text = '%s\n\n%s' % (text, '\n\n'.join(notes))
        super()._store_sanitized_response(text)
        self.write({'cited_repealed': bool(repealed), 'cited_nothing': uncited})
