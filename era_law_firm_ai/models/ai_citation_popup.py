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

from odoo import api, models

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
            'status': law.status or '',
            'official_url': law.source_url or '',
            'download_url': '/web/content/%s' % attachment.id,
        }
