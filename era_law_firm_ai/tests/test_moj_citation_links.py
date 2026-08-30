"""Citations lead to the copy we indexed, deliberately.

Pointing them at laws.moj.gov.sa was tried and reverted: the ministry's deep
links do not reliably resolve, and a citation that leads nowhere is worse than
one leading to the text the answer was actually drawn from.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMojCitationLinks(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env['ai.agent'].create({
            'name': 'Research', 'llm_model': 'custom_llm/custom',
        })

    def _statute_source(self, status='indexed'):
        attachment = self.env['ir.attachment'].create({
            'name': 'نظام المحاماة.txt',
            'raw': 'المادة الأولى: مهنة المحاماة.'.encode(),
            'mimetype': 'text/plain',
        })
        return self.env['ai.agent.source'].create({
            'agent_id': self.agent.id, 'attachment_id': attachment.id,
            'type': 'binary', 'status': status,
        })

    def test_the_citation_points_at_the_indexed_copy(self):
        source = self._statute_source()
        answer = f"يجوز ذلك [SOURCE:{source.attachment_id.id}]."
        rendered = self.agent._get_llm_response_with_sources([answer])[0]
        self.assertIn(f"/web/content/{source.attachment_id.id}", rendered)
        self.assertNotIn("laws.moj.gov.sa", rendered)

    def test_the_corpus_sync_leaves_source_urls_alone(self):
        """The official URL stays on moj.law and goes no further."""
        source = self._statute_source()
        law = self.env['moj.law'].create({
            'law_id': 'test-law-1', 'name': 'نظام المحاماة',
            'source_url': 'https://laws.moj.gov.sa/ar/legislation/abc',
            'source_ids': [(4, source.id)],
        })
        self.assertTrue(law.source_url, 'the official URL is still recorded')
        self.assertFalse(source.url, 'but it is not what a citation links to')

    def test_retry_of_a_file_source_with_a_url_still_re_embeds(self):
        """Core picks the retry path from `url`; a file source must not be
        handed to the scraper, which ignores it and leaves it stuck."""
        source = self._statute_source(status='failed')
        source.url = 'https://example.invalid/pasted-by-hand'
        source.action_retry_failed_source()
        self.assertEqual(source.status, 'processing')
