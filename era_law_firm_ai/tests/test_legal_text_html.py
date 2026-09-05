"""The corpus reads as a statute, not as the string an embedding needed.

The text is stored flat on purpose — one long string is what gets embedded —
and every marker in it is structure written for a machine: the instrument's
name repeated in front of each article, definitions as dashed lines, section
titles wrapped in equals signs. Put back deterministically: no model is asked,
so the same text always comes out the same way, and a statute that happens to
contain a tag is text rather than markup.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.era_law_firm_ai.tools.legal_text_html import format_legal_text


@tagged('post_install', '-at_install')
class TestLegalTextHtml(TransactionCase):

    def test_a_section_becomes_a_heading(self):
        html = format_legal_text('== التعريفات ==')
        self.assertIn('<h5', html)
        self.assertIn('التعريفات', html)
        self.assertNotIn('==', html)

    def test_an_article_keeps_its_number_and_drops_the_repeated_name(self):
        """Every article carries the instrument's name in the source. Inside
        that instrument it is noise on every heading."""
        html = format_legal_text(
            '«اللائحة التنفيذية لنظام المحاكم التجارية» — المادة الأولى:: يقصد بالألفاظ الآتية.')
        self.assertIn('<h6', html)
        self.assertIn('المادة الأولى', html)
        self.assertIn('يقصد بالألفاظ الآتية.', html)
        self.assertNotIn('اللائحة التنفيذية لنظام المحاكم التجارية', html)

    def test_a_definition_shows_the_term_it_defines(self):
        html = format_legal_text('- المجلس: المجلس الأعلى للقضاء.')
        self.assertIn('<ul', html)
        self.assertIn('<b>المجلس:</b>', html)

    def test_a_lettered_branch_keeps_its_letter(self):
        """An article refers to «الفقرة (ب)», so the letter is not decoration."""
        html = format_legal_text('أ - النص الوارد في الاتفاقية.\n\nب - النص الإجرائي الخاص.')
        self.assertIn('<b>أ -</b>', html)
        self.assertIn('<b>ب -</b>', html)
        self.assertEqual(html.count('<ul'), 1, 'one list, not one per line')

    def test_the_line_the_sync_writes_is_not_shown_twice(self):
        """Title, kind and status open every document, and the citation card
        already carries them."""
        html = format_legal_text(
            'نظام المرافعات — النوع: نظام — الحالة: ساري\n\n== الباب الأول ==')
        self.assertNotIn('النوع:', html)
        self.assertIn('الباب الأول', html)

    def test_a_statute_that_contains_markup_is_text(self):
        html = format_legal_text('يقصد بالعلامة <script>alert(1)</script> ما يميز البضاعة.')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(format_legal_text(''), '')
        self.assertEqual(format_legal_text('   \n\n  '), '')
