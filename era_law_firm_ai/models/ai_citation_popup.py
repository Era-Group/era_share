"""A citation you can open without leaving the answer.

Odoo numbers the sources an agent used and links each one to the file it came
from, with target="_blank". That is right for a general assistant and wrong
here: a lawyer reading an answer about a deadline wants to check the article
the answer leaned on, and losing the answer to a new tab to do it makes them
choose between the two.

So the link is marked instead — same href, so it still works if the browser
never runs our script — and the interface opens the statute over the chat.
Only for the agents that carry the corpus; every other AI chat in the database
keeps the behaviour Odoo wrote.
"""
import re

from odoo import _, api, fields, models

# Odoo builds the citation itself, in ai/utils/ai_citation.py. Matching what it
# builds rather than rewriting the builder means a change on their side leaves
# a link that still opens — in a tab, as before — instead of a broken one.
CITATION_LINK = re.compile(
    r'(<sup><a\s+href="[^"]*")\s+target="_blank"\s+rel="noreferrer noopener"')
CITATION_MARK = r'\1 class="o_era_citation"'


class LegalCitationAgent(models.Model):
    _inherit = 'ai.agent'

    def _get_llm_response_with_sources(self, llm_response):
        messages = super()._get_llm_response_with_sources(llm_response)
        if not self.moj_corpus_target:
            return messages
        return [CITATION_LINK.sub(CITATION_MARK, message) for message in messages]


class LegalCitationSource(models.Model):
    _inherit = 'ai.agent.source'

    @api.model
    def era_citation_document(self, attachment_id):
        """The text behind one citation, for the dialog that shows it.

        Read as the user: a citation Odoo judged accessible enough to print is
        readable, and anything else raises here rather than being served.
        """
        attachment = self.env['ir.attachment'].browse(int(attachment_id))
        attachment.check_access('read')
        source = self.search([('attachment_id', '=', attachment.id)], limit=1)
        law = self.env['moj.law'].search(
            [('source_ids.attachment_id', '=', attachment.id)], limit=1)
        text = ''
        if attachment.mimetype in ('text/plain', 'text/markdown'):
            text = (attachment.raw or b'').decode('utf-8', 'replace')
        return {
            'name': source.name or attachment.name or '',
            'text': text,
            'reference': law._era_citation_reference() if law else [],
            'citation_line': law._era_citation_line() if law else '',
            'download_url': '/web/content/%s' % attachment.id,
        }


class LegalCitationLaw(models.Model):
    """How a statute identifies itself, for someone who will go and look it up.

    The link to the Ministry's page was the obvious thing to offer and the
    wrong one: those addresses are rotated, and a citation that leads to a dead
    page is worse than none. What survives a rotation is what the lawyer would
    type into the Ministry's own search — the title, what kind of instrument it
    is, whether it is still in force, how long it runs. Not the Ministry's
    internal identifier: it means nothing to a reader and nothing in a
    memorandum. And the date our copy was taken, which is the honest part: the
    answer was drawn from this text on that day.
    """
    _inherit = 'moj.law'

    def _era_citation_reference(self):
        """Label/value pairs, in the order a citation is written."""
        self.ensure_one()
        rows = [
            (_('Instrument'), self.name or ''),
            (_('Type'), self.law_type or ''),
            (_('Status'), self.status or ''),
        ]
        if self.article_count:
            rows.append((_('Article count'), str(self.article_count)))
        if self.last_synced:
            rows.append((_('This copy taken'), fields.Date.to_string(self.last_synced.date())))
        return [{'label': label, 'value': value} for label, value in rows if value]

    def _era_citation_line(self):
        """The same thing on one line, for pasting into a memorandum."""
        self.ensure_one()
        return ' — '.join('%s: %s' % (row['label'], row['value'])
                          for row in self._era_citation_reference())
