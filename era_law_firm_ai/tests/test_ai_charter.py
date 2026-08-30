"""The office's standing legal instructions, and asking again."""

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCharter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [
            (4, cls.env.ref('era_law_firm.group_legal_manager').id),
            (4, cls.env.ref('era_law_firm_ai.group_legal_ai_user').id)]
        cls.env.company.legal_ai_enabled = True
        cls.agent = cls.env.ref('era_law_firm_ai.agent_drafting')
        cls.agent.write({'legal_processing_location': 'KSA', 'legal_retention_policy': 'None.',
                         'legal_max_classification': 'confidential', 'legal_approved': True})
        partner = cls.env['res.partner'].create({'name': 'موكل'})
        cls.case = cls.env['legal.case'].create({
            'client_id': partner.id, 'case_type': 'litigation',
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id})
        cls.charter = cls.env.ref('era_law_firm_ai.charter_default')

    def _sent_request(self, capture=None, instructions='ركّز على الاختصاص.'):
        request = self.env['legal.ai.request'].create({
            'agent_id': self.agent.id, 'case_id': self.case.id, 'purpose': 'مسودة',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)],
            'input_payload': instructions})
        request.action_approve()

        def fake(agent_self, prompt, chat_history=None, extra_system_context=""):
            if capture is not None:
                capture['context'] = extra_system_context
                capture['prompt'] = prompt
            return ['## الرأي\n\nالدفع **وجيه**.']

        with patch.object(type(self.agent), '_generate_response', fake):
            request.action_send()
        return request

    def test_a_charter_ships_with_the_module(self):
        self.assertTrue(self.charter.body.strip())
        self.assertIn('يخلي المكتب مسؤوليته', self.charter.disclaimer)

    def test_the_charter_reaches_the_agent_as_system_context(self):
        captured = {}
        self._sent_request(captured)
        self.assertIn('مستشار قانوني', captured['context'])
        self.assertIn('الاستشهاد الخاطئ', captured['context'],
                      'the citation discipline must be in force')

    def test_it_applies_whichever_agent_is_used(self):
        summary = self.env.ref('era_law_firm_ai.agent_summary')
        summary.write({'legal_processing_location': 'KSA', 'legal_retention_policy': 'None.',
                       'legal_max_classification': 'confidential', 'legal_approved': True})
        captured = {}
        request = self.env['legal.ai.request'].create({
            'agent_id': summary.id, 'case_id': self.case.id, 'purpose': 'تلخيص',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)]})
        request.action_approve()

        def fake(agent_self, prompt, chat_history=None, extra_system_context=""):
            captured['context'] = extra_system_context
            return ['ملخص']

        with patch.object(type(summary), '_generate_response', fake):
            request.action_send()
        self.assertIn('مستشار قانوني', captured['context'])

    def test_the_notice_is_appended_by_the_system(self):
        """A notice that only appears when the model remembers it is not a notice."""
        request = self._sent_request()
        self.assertIn('يخلي المكتب مسؤوليته', request.sanitized_response)

    def test_the_charter_in_force_is_recorded_on_the_request(self):
        request = self._sent_request()
        self.assertEqual(request.charter_id, self.charter)

    def test_editing_the_charter_changes_what_is_sent(self):
        self.charter.body = self.charter.body + '\n\nقاعدة إضافية للاختبار.'
        captured = {}
        self._sent_request(captured)
        self.assertIn('قاعدة إضافية للاختبار', captured['context'])


@tagged('post_install', '-at_install')
class TestResend(TestCharter):

    def test_asking_again_reopens_the_same_request(self):
        request = self._sent_request()
        request_id = request.id
        request.action_resend()
        self.assertEqual(request.id, request_id, 'the same record must come back, not a new one')
        self.assertEqual(request.state, 'draft')
        self.assertFalse(request.consent_user_id, 'a second dispatch needs its own consent')
        self.assertFalse(request.sanitized_response)
        self.assertFalse(request.payload_hash)

    def test_what_was_already_sent_is_kept_as_history(self):
        request = self._sent_request()
        original_hash = request.payload_hash
        original_answer = request.sanitized_response

        request.action_resend()

        self.assertEqual(request.attempt_count, 1)
        attempt = request.attempt_ids[0]
        self.assertEqual(attempt.payload_hash, original_hash)
        self.assertEqual(attempt.sanitized_response, original_answer)
        self.assertTrue(attempt.consent_user_id, 'who consented to that dispatch is part of the record')

    def test_the_instructions_come_back_for_editing(self):
        """They are discarded on dispatch, so the redacted copy is what a reopen restores."""
        request = self._sent_request(instructions='ركّز على بند الإنهاء.')
        self.assertIn('بند الإنهاء', request.instructions_sent)
        request.action_resend()
        self.assertIn('بند الإنهاء', request.input_payload)

    def test_each_send_adds_to_the_history(self):
        request = self._sent_request()
        request.action_resend()
        request.input_payload = 'تعليمات مختلفة.'
        request.action_approve()
        with patch.object(type(self.agent), '_generate_response',
                          lambda *a, **k: ['## الثاني']):
            request.action_send()
        request.action_resend()
        self.assertEqual(request.attempt_count, 2)
        # what matters is that both dispatches are numbered and kept; the order the
        # history is listed in is a display concern the model's _order handles
        self.assertEqual(sorted(request.attempt_ids.mapped('sequence_number')), [1, 2])

    def test_a_dispatch_record_cannot_be_altered_or_removed(self):
        request = self._sent_request()
        request.action_resend()
        attempt = request.attempt_ids[0]
        with self.assertRaises(AccessError):
            attempt.write({'payload_hash': 'tampered'})
        with self.assertRaises(AccessError):
            attempt.unlink()

    def test_a_request_that_was_never_sent_cannot_be_reopened(self):
        request = self.env['legal.ai.request'].create({
            'agent_id': self.agent.id, 'case_id': self.case.id, 'purpose': 'مسودة',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)]})
        with self.assertRaises(UserError):
            request.action_resend()


@tagged('post_install', '-at_install')
class TestReferencePortal(TestCharter):
    """One portal, named once, anchoring every answer."""

    def test_the_dead_per_statute_links_are_gone(self):
        self.assertNotIn('url', self.env['legal.legislation']._fields,
                         'the per-statute link field should not exist any more')

    def test_every_row_shows_the_one_portal(self):
        """Moved off the default first: comparing two reads of the same literal would
        pass even if the compute returned a hard-coded string."""
        self.charter.reference_portal = 'https://example.gov.sa/'
        rows = self.env['legal.legislation'].search([], limit=5)
        self.assertTrue(rows)
        for row in rows:
            row.invalidate_recordset(['portal_url'])
            self.assertEqual(row.portal_url, 'https://example.gov.sa/')

    def test_the_portal_cannot_be_blank_or_whitespace(self):
        """required only stops NULL, and the url widget does not trim."""
        for blank in (False, '', '   '):
            with self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self.charter.reference_portal = blank

    def test_a_company_charter_beats_the_shared_one(self):
        """Ordering by a many2one delegated to res.company._order, so the shared
        charter's NULL company sorted first and won every time."""
        Charter = self.env['legal.ai.charter']
        shared = Charter._for_company(self.env.company)
        self.assertFalse(shared.company_id, 'the seeded charter is the shared one')
        own = Charter.create({
            'name': 'ميثاق الشركة', 'company_id': self.env.company.id,
            'body': 'تعليمات خاصة بالشركة', 'disclaimer': 'تنبيه خاص',
            'reference_portal': 'https://own.example.sa/'})
        self.assertEqual(Charter._for_company(self.env.company), own)
        self.assertEqual(Charter._reference_portal(self.env.company), 'https://own.example.sa/')

    def test_a_company_without_its_own_charter_falls_back(self):
        other = self.env['res.company'].create({'name': 'فرع آخر'})
        picked = self.env['legal.ai.charter']._for_company(other)
        self.assertTrue(picked)
        self.assertFalse(picked.company_id)

    def test_the_portal_reaches_the_agent(self):
        captured = {}
        self._sent_request(captured)
        context = captured['context']
        self.assertIn('laws.moj.gov.sa', context)
        self.assertIn('المرجع الوحيد', context)

    def test_it_forbids_reasoning_from_another_jurisdiction(self):
        captured = {}
        self._sent_request(captured)
        self.assertIn('ولاية قضائية أخرى', captured['context'])

    def test_it_still_says_the_agent_cannot_open_it(self):
        """Naming a portal must not read as giving the agent access to it.

        Asserted on wording unique to the appended sentence: the seeded body already
        says 'لا تستطيع فتح روابط', so the looser phrase passed with the sentence gone.
        """
        captured = {}
        self._sent_request(captured)
        self.assertIn('لا تستطيع فتح هذا الرابط', captured['context'])
        self.assertIn('للتحقق البشري', captured['context'])

    def test_changing_the_portal_changes_what_is_sent(self):
        self.charter.reference_portal = 'https://example.gov.sa/'
        captured = {}
        self._sent_request(captured)
        self.assertIn('example.gov.sa', captured['context'])


@tagged('post_install', '-at_install')
class TestLegislationRegister(TestCharter):
    """The register is a worklist, and it has to read like one."""

    def test_the_seeded_references_are_unique(self):
        rows = self.env['legal.legislation'].search([])
        ids = rows.mapped('moj_id')
        self.assertEqual(len(ids), len(set(ids)), 'the register must not hold the same statute twice')
        names = rows.mapped('name')
        self.assertEqual(len(names), len(set(names)))

    def test_the_same_statute_cannot_be_registered_twice(self):
        existing = self.env['legal.legislation'].search([], limit=1)
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env['legal.legislation'].create({
                    'name': 'مكرر', 'moj_id': existing.moj_id})

    def test_display_name_does_not_repeat_the_reference(self):
        """The placeholder name ends with the reference; appending it again produced
        'نظام (430...) (430...)' in tags and breadcrumbs."""
        row = self.env['legal.legislation'].search([('moj_id', '!=', False)], limit=1)
        self.assertEqual(row.display_name.count(row.moj_id), 1)

    def test_a_named_statute_still_shows_its_reference(self):
        row = self.env['legal.legislation'].search([('moj_id', '!=', False)], limit=1)
        row.name = 'نظام المرافعات الشرعية'
        self.assertIn(row.moj_id, row.display_name)

    def test_the_seeded_rows_are_flagged_as_unfinished(self):
        """Sixty-nine placeholders should read as work owed, not as named statutes."""
        rows = self.env['legal.legislation'].search([])
        self.assertTrue(rows)
        self.assertFalse(any(rows.mapped('title_confirmed')))
        self.assertFalse(any(rows.mapped('source_attached')))
