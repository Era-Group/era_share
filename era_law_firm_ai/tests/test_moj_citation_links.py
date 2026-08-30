"""A citation in a legal answer must lead to the ministry, not to our copy."""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMojCitationLinks(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env['ai.agent'].create({
            'name': 'Research', 'llm_model': 'custom_llm/custom',
        })
        cls.official = 'https://laws.moj.gov.sa/ar/legislation/MylPJFcFKGmwPp-wwS196g'

    def _statute_with_source(self, url=None, status='indexed'):
        attachment = self.env['ir.attachment'].create({
            'name': 'نظام المحاماة.txt',
            'raw': 'المادة الأولى: مهنة المحاماة.'.encode(),
            'mimetype': 'text/plain',
        })
        source = self.env['ai.agent.source'].create({
            'agent_id': self.agent.id, 'attachment_id': attachment.id,
            'type': 'binary', 'status': status,
        })
        law = self.env['moj.law'].create({
            'law_id': 'test-law-1', 'name': 'نظام المحاماة',
            'source_url': url if url is not None else self.official,
            'source_ids': [(4, source.id)],
        })
        return law, source

    def test_the_official_url_reaches_the_source(self):
        law, source = self._statute_with_source()
        self.assertFalse(source.url, "a file-backed source starts with no URL")
        law._apply_official_url_to_sources()
        self.assertEqual(source.url, self.official)

    def test_a_statute_without_an_official_url_is_left_alone(self):
        law, source = self._statute_with_source(url=False)
        law._apply_official_url_to_sources()
        self.assertFalse(source.url, "nothing to apply, nothing written")

    def test_the_citation_link_is_the_ministry_page(self):
        """This is the behaviour the whole change exists for."""
        law, source = self._statute_with_source()
        law._apply_official_url_to_sources()
        answer = f"يجوز ذلك [SOURCE:{source.attachment_id.id}]."
        rendered = self.agent._get_llm_response_with_sources([answer])[0]
        self.assertIn(self.official, rendered,
                      "the citation must point at laws.moj.gov.sa")
        self.assertNotIn(f"/web/content/{source.attachment_id.id}", rendered,
                         "and not at the copy indexed inside Odoo")

    def test_retry_of_a_file_source_still_re_embeds(self):
        """Core picks the retry cron from `url`; a file source must not be
        handed to the scraper, which ignores it and leaves it stuck."""
        law, source = self._statute_with_source(status='failed')
        law._apply_official_url_to_sources()
        self.assertTrue(source.url, "precondition: the source now carries a URL")
        source.action_retry_failed_source()
        self.assertEqual(source.status, 'processing')
        self.assertEqual(source.url, self.official,
                         "the URL survives a retry")
