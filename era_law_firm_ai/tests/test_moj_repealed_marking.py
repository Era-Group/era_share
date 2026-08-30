"""A repealed statute must announce itself in every retrieved chunk.

Ten of the 75 statutes in the corpus are repealed, and the live agent was
returning one of them — اللائحة التنفيذية لنظام المحاماة — as the top hit for
a question about current requirements, with nothing to say it no longer
applies. Retrieval returns chunks, so a banner at the top of the document
never reaches the middle of it.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRepealedMarking(TransactionCase):
    def _render(self, status):
        meta = {'law_name': 'نظام المحاماة', 'law_type': 'نظام', 'law_status': status}
        articles = [
            {'article_label': 'المادة الأولى', 'text': 'نص المادة الأولى.', 'order_index': 1},
            {'article_label': 'المادة العاشرة', 'text': 'نص المادة العاشرة.', 'order_index': 10},
        ]
        return self.env['moj.law']._render_law_text(meta, articles)

    def test_every_article_of_a_repealed_statute_carries_the_status(self):
        text = self._render('ملغي')
        article_lines = [l for l in text.split('\n\n') if 'المادة' in l]
        self.assertEqual(len(article_lines), 2, 'both articles are rendered')
        for line in article_lines:
            self.assertIn('[ملغي]', line,
                          'a chunk from anywhere in the document must say so')

    def test_a_repealed_statute_opens_with_a_warning(self):
        self.assertIn('لا يُستشهد به كنص ساري', self._render('ملغي'))

    def test_a_current_statute_is_not_cluttered(self):
        text = self._render('ساري')
        self.assertNotIn('[ساري]', text, 'the marker is for exceptions only')
        self.assertNotIn('لا يُستشهد', text)
        self.assertIn('«نظام المحاماة» — المادة الأولى', text)

    def test_a_statute_pending_entry_into_force_is_also_marked(self):
        """"ساري من تاريخ" is not yet in force — one statute is in that state."""
        text = self._render('ساري من تاريخ')
        self.assertIn('[ساري من تاريخ]', text)
