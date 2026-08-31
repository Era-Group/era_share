"""What the answer cited is checked, not assumed.

The statute text marks every article of a repealed law, and the research agent
is told to answer only from its sources. Both are prompt paragraphs. The
failures they guard against are invisible in the output: a repealed article
reads exactly like a current one once quoted, and an answer with no citation
reads no differently from a grounded one.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCitationAudit(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env.ref('era_law_firm_ai.agent_research')
        cls.client = cls.env['res.partner'].create({'name': 'شركة الأفق'})
        cls.case = cls.env['legal.case'].create({
            'name': 'قضية', 'client_id': cls.client.id, 'lawyer_id': cls.env.user.id,
            'case_type': 'litigation', 'company_id': cls.env.company.id,
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id,
        })

    def _statute(self, name, status):
        attachment = self.env['ir.attachment'].create({
            'name': name, 'raw': f'«{name}» — المادة الأولى'.encode(),
            'mimetype': 'text/plain'})
        source = self.env['ai.agent.source'].create({
            'agent_id': self.agent.id, 'attachment_id': attachment.id,
            'type': 'binary', 'status': 'indexed', 'is_active': True})
        law = self.env['moj.law'].create({
            'law_id': f'test-{name}', 'name': name, 'status': status,
            'source_ids': [(4, source.id)]})
        return law, attachment

    def _request(self):
        return self.env['legal.ai.request'].create({
            'agent_id': self.agent.id, 'case_id': self.case.id, 'purpose': 'بحث'})

    def test_citing_a_repealed_statute_is_announced(self):
        law, attachment = self._statute('نظام ملغي للاختبار', 'ملغي')
        request = self._request()
        request._store_sanitized_response(f'الحكم كذا [SOURCE:{attachment.id}].')
        self.assertTrue(request.cited_repealed)
        self.assertIn('غير سارٍ', request.sanitized_response)
        self.assertIn(law.name, request.sanitized_response)

    def test_citing_a_current_statute_is_left_alone(self):
        _law, attachment = self._statute('نظام سارٍ للاختبار', 'ساري')
        request = self._request()
        request._store_sanitized_response(f'الحكم كذا [SOURCE:{attachment.id}].')
        self.assertFalse(request.cited_repealed)
        self.assertNotIn('غير سارٍ', request.sanitized_response)

    def test_a_restricted_agent_answering_with_no_citation_is_flagged(self):
        self.assertTrue(self.agent.restrict_to_sources, 'precondition')
        request = self._request()
        request._store_sanitized_response('الحكم كذا، بلا إسناد.')
        self.assertTrue(request.cited_nothing)
        self.assertIn('لم يستشهد', request.sanitized_response)

    def test_an_unrestricted_agent_needs_no_citation(self):
        drafting = self.env.ref('era_law_firm_ai.agent_drafting')
        self.assertFalse(drafting.restrict_to_sources, 'precondition')
        request = self.env['legal.ai.request'].create({
            'agent_id': drafting.id, 'case_id': self.case.id, 'purpose': 'صياغة'})
        request._store_sanitized_response('مسودة مذكرة بلا استشهاد.')
        self.assertFalse(request.cited_nothing)

    def test_several_ids_in_one_tag_are_all_read(self):
        law, repealed = self._statute('نظام ملغي ٢', 'ملغي')
        _current, fine = self._statute('نظام سارٍ ٢', 'ساري')
        request = self._request()
        request._store_sanitized_response(f'الحكم [SOURCE:{fine.id}, {repealed.id}].')
        self.assertTrue(request.cited_repealed,
                        'a repealed statute alongside a current one still counts')
        self.assertIn(law.name, request.sanitized_response)

    def test_an_empty_answer_is_not_flagged_as_uncited(self):
        """A failed call is a different problem and has its own reporting."""
        request = self._request()
        request._store_sanitized_response('')
        self.assertFalse(request.cited_nothing)
