"""A citation should open over the answer, not carry the lawyer away from it.

Odoo prints the sources behind an answer as numbered links to the files they
came from, opening in a new tab. The link is marked here instead, and the
interface shows the text over the chat — so what is worth holding is that the
mark is applied to this module's agents and to no others, and that the text
behind a citation is served to the reader only if it was theirs to read.
"""
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCitationPopup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي الاستشهاد', 'login': 'citation_reader',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('era_law_firm.group_legal_lawyer').id,
                cls.env.ref('era_law_firm_ai.group_legal_ai_user').id])]})
        cls.agent = cls.env.ref('era_law_firm_ai.agent_research')
        cls.attachment = cls.env['ir.attachment'].create({
            'name': 'نظام المرافعات الشرعية.txt',
            'raw': 'المادة الأولى: تسري أحكام هذا النظام على الدعاوى.'.encode(),
            'mimetype': 'text/plain'})
        cls.source = cls.env['ai.agent.source'].create({
            'agent_id': cls.agent.id, 'attachment_id': cls.attachment.id,
            'name': 'نظام المرافعات الشرعية', 'type': 'binary'})
        cls.answer = 'المدة ثلاثون يوماً [SOURCE:%s].' % cls.attachment.id

    def test_the_citation_is_marked_and_does_not_leave_the_answer(self):
        [content] = self.agent._get_llm_response_with_sources([self.answer])
        self.assertIn('o_era_citation', content)
        self.assertNotIn('target="_blank"', content)
        self.assertIn('/web/content/%s' % self.attachment.id, content,
                      'the link still works without the script')

    def test_every_other_agent_in_the_database_is_left_alone(self):
        """This module's agents carry the firm's corpus; the rest belong to
        whoever installed them, and their chats behave as Odoo wrote them."""
        general = self.env.ref('ai.ai_default_agent')
        source = self.source.copy({'agent_id': general.id})
        self.assertTrue(source)
        [content] = general._get_llm_response_with_sources([self.answer])
        self.assertNotIn('o_era_citation', content)

    def test_the_dialog_is_given_the_text_and_what_to_cite(self):
        """Not a link to the Ministry: those addresses are rotated, and a dead
        one is worse than none. What survives is what a lawyer would type into
        the Ministry's own search."""
        self.env['moj.law'].create({
            'name': 'نظام المرافعات الشرعية', 'law_id': 'citation-test',
            'law_type': 'نظام', 'status': 'ساري', 'article_count': 245,
            'source_url': 'https://laws.moj.gov.sa/ar/legislation/test',
            'source_ids': [(6, 0, self.source.ids)]})
        document = self.env['ai.agent.source'].with_user(self.lawyer)\
            .era_citation_document(self.attachment.id)
        self.assertIn('المادة الأولى', document['html'])
        self.assertEqual(document['name'], 'نظام المرافعات الشرعية')
        values = {row['label']: row['value'] for row in document['reference']}
        self.assertNotIn('Instrument', values,
                         'the dialog is titled with the name; the card would repeat it')
        self.assertTrue(document['citation_line'].startswith('نظام المرافعات الشرعية'),
                        'but a line pasted into a memorandum has no title above it')
        self.assertEqual(values['Status'], 'ساري', 'a repealed statute is the point')
        self.assertNotIn('Ministry reference', values,
                         'an internal identifier means nothing to a reader')
        self.assertIn('245', values['Article count'])
        self.assertNotIn('http', document['citation_line'],
                         'a line to paste into a memorandum, not an address')

    def test_it_serves_nothing_the_reader_could_not_already_open(self):
        """The dialog reads as the user, so a citation is not a way around the
        rules that decide what they may see."""
        private = self.env['ir.attachment'].create({
            'name': 'إعداد داخلي.txt', 'raw': b'secret',
            'res_model': 'ir.config_parameter', 'res_id': 1})
        with self.assertRaises(AccessError):
            self.env['ai.agent.source'].with_user(self.lawyer)\
                .era_citation_document(private.id)
