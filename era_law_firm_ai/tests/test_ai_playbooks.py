"""Common requests, one click from the case — without loosening anything.

A playbook assembles the request; it must not get to skip the policy. These
tests hold the two halves: the assembled request is right (agent, data,
instructions, document), and what reaches the agent is still redacted, still
whitelisted, still consented to, and still blind to what the lawyer may not see.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAIPlaybooks(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.legal_ai_enabled = True
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي الأوامر', 'login': 'playbook_lawyer',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('era_law_firm.group_legal_lawyer').id,
                cls.env.ref('era_law_firm_ai.group_legal_ai_user').id])]})
        cls.colleague = cls.env['res.users'].create({
            'name': 'زميل', 'login': 'playbook_colleague',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('era_law_firm.group_legal_lawyer').id])]})
        for xmlid in ('agent_case_assistant', 'agent_drafting', 'agent_summary',
                      'agent_research', 'agent_contract_review'):
            cls.env.ref('era_law_firm_ai.%s' % xmlid).write({
                'legal_processing_location': 'KSA',
                'legal_retention_policy': 'Not retained.',
                'legal_approved': True})
        client = cls.env['res.partner'].create({'name': 'موكّل الأوامر'})
        wizard = cls.env['legal.intake.wizard'].with_user(cls.lawyer).create({
            'client_id': client.id, 'case_type': 'litigation',
            'lawyer_id': cls.lawyer.id, 'engagement_type': 'none',
            'opponent_ids': [(6, 0, cls.env['res.partner'].create({'name': 'خصم الأوامر'}).ids)]})
        cls.case = cls.env['legal.case'].browse(wizard.action_open_case()['res_id'])
        now = fields.Datetime.now()
        cls.env['legal.hearing'].create({
            'name': 'الجلسة الأولى', 'case_id': cls.case.id, 'lawyer_id': cls.lawyer.id,
            'start_datetime': now - timedelta(days=10), 'stop_datetime': now - timedelta(days=10, hours=-1),
            'state': 'done', 'hijri_date': '1447/09/01',
            'outcome': 'قدّم المدعي لائحته وطلبت المحكمة الرد خلال أسبوعين، هاتف الخصم 0551234567',
            'company_id': cls.env.company.id})
        cls.env['legal.deadline'].create({
            'name': 'تقديم المذكرة الجوابية', 'case_id': cls.case.id, 'user_id': cls.lawyer.id,
            'deadline_date': fields.Date.today() + timedelta(days=4), 'source': 'قرار المحكمة',
            'state': 'confirmed', 'company_id': cls.env.company.id})
        # Attached to the case the way the form does it, so access follows the
        # case's own rules instead of the administrator-only default.
        attachment = cls.env['ir.attachment'].create({
            'name': 'لائحة.txt', 'raw': 'نص اللائحة'.encode(), 'mimetype': 'text/plain',
            'res_model': 'legal.case', 'res_id': cls.case.id})
        cls.document = cls.env['legal.document'].create({
            'name': 'لائحة الدعوى', 'case_id': cls.case.id, 'attachment_id': attachment.id,
            'document_type': 'pleading', 'owner_id': cls.lawyer.id})
        secret = cls.env['ir.attachment'].create({
            'name': 'سري.txt', 'raw': b'x', 'mimetype': 'text/plain',
            'res_model': 'legal.case', 'res_id': cls.case.id})
        cls.restricted = cls.env['legal.document'].create({
            'name': 'مذكرة داخلية مقيَّدة', 'case_id': cls.case.id, 'attachment_id': secret.id,
            'owner_id': cls.colleague.id, 'restricted': True,
            'allowed_user_ids': [(6, 0, cls.colleague.ids)]})

    def _wizard(self, xmlid, **values):
        values.setdefault('case_id', self.case.id)
        values['playbook_id'] = self.env.ref('era_law_firm_ai.%s' % xmlid).id
        return self.env['legal.ai.playbook.wizard'].with_user(self.lawyer).create(values)

    def _run(self, xmlid, **values):
        action = self._wizard(xmlid, **values).action_run()
        return self.env['legal.ai.request'].with_user(self.lawyer).browse(action['res_id'])

    # ------------------------------------------------------------ the catalogue
    def test_the_playbooks_ship_and_only_share_catalogue_entries(self):
        playbooks = self.env['legal.ai.playbook'].search([('is_shipped', '=', True)])
        self.assertGreaterEqual(len(playbooks), 15)
        allowed = self.env['legal.ai.request']._ALLOWED_FIELDS
        for playbook in playbooks:
            self.assertTrue(playbook.agent_id, playbook.name)
            self.assertTrue(playbook.instructions, playbook.name)
            for entry in playbook.field_ids | playbook.optional_field_ids:
                self.assertIn(entry.technical_name, allowed, '%s → %s' % (playbook.name, entry.name))

    def test_a_playbook_never_ticks_a_sensitive_entry_on_its_own(self):
        for playbook in self.env['legal.ai.playbook'].search([('is_shipped', '=', True)]):
            self.assertFalse(playbook.field_ids.filtered('sensitive'),
                             '%s ticks something sensitive by default' % playbook.name)

    def test_the_hearing_log_renders_what_happened(self):
        request = self._run('playbook_client_report')
        payload = request.payload_preview
        self.assertIn('الجلسة الأولى', payload)
        self.assertIn('1447/09/01', payload)
        self.assertIn('طلبت المحكمة الرد', payload)
        self.assertIn('تقديم المذكرة الجوابية', payload)
        self.assertIn('لائحة الدعوى', payload)

    def test_the_log_is_redacted_like_everything_else(self):
        request = self._run('playbook_client_report')
        self.assertNotIn('0551234567', request.payload_preview)
        self.assertIn('[REDACTED-PHONE]', request.payload_preview)

    def test_a_restricted_document_is_not_listed_to_someone_who_cannot_open_it(self):
        request = self._run('playbook_client_report')
        self.assertNotIn('مذكرة داخلية مقيَّدة', request.payload_preview)
        as_colleague = self.env['legal.ai.playbook.wizard'].with_user(self.colleague)
        # the colleague is not an AI user and cannot raise requests at all
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            as_colleague.create({'case_id': self.case.id,
                                 'playbook_id': self.env.ref('era_law_firm_ai.playbook_client_report').id})

    # --------------------------------------------------------------- the wizard
    def test_the_request_is_assembled_from_the_playbook(self):
        playbook = self.env.ref('era_law_firm_ai.playbook_case_brief')
        request = self._run('playbook_case_brief')
        self.assertEqual(request.state, 'draft', 'consent is still the lawyer\'s to give')
        self.assertEqual(request.agent_id, playbook.agent_id)
        self.assertEqual(request.playbook_id, playbook)
        self.assertEqual(request.field_ids, playbook.field_ids)
        self.assertIn('موجزاً داخلياً', request.input_payload)
        self.assertFalse(request.consent_user_id)

    def test_sensitive_extras_come_only_when_asked_for(self):
        playbook = self.env.ref('era_law_firm_ai.playbook_case_brief')
        plain = self._run('playbook_case_brief')
        self.assertFalse(plain.field_ids & playbook.optional_field_ids)
        with_extras = self._run('playbook_case_brief', include_extras=True)
        self.assertEqual(with_extras.field_ids & playbook.optional_field_ids,
                         playbook.optional_field_ids)

    def test_a_task_that_needs_the_lawyers_facts_insists_on_them(self):
        with self.assertRaises(UserError):
            self._wizard('playbook_risk_assessment').action_run()
        request = self._run('playbook_risk_assessment', question='الخصم لم يسلّم البضاعة')
        self.assertIn('الخصم لم يسلّم البضاعة', request.input_payload)

    def test_a_task_that_works_on_a_document_insists_on_one(self):
        with self.assertRaises(UserError):
            self._wizard('playbook_summarise_document').action_run()
        request = self._run('playbook_summarise_document', document_id=self.document.id)
        self.assertEqual(request.document_id, self.document)
        self.assertIn('نص اللائحة', request.payload_preview)

    def test_a_task_for_another_case_type_is_refused(self):
        with self.assertRaises(UserError):
            self._wizard('playbook_execution_plan').action_run()

    def test_the_case_offers_the_wizard(self):
        action = self.case.with_user(self.lawyer).action_ask_ai()
        self.assertEqual(action['res_model'], 'legal.ai.playbook.wizard')
        self.assertEqual(action['context']['default_case_id'], self.case.id)

    # ------------------------------------------------------------- the dispatch
    def test_the_agent_receives_the_rendered_log_and_nothing_secret(self):
        request = self._run('playbook_client_report')
        request.action_approve()
        captured = {}

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=''):
            captured['prompt'] = prompt
            return ['تقرير المجريات.']

        agent_model = type(self.env['ai.agent'])
        with patch.object(agent_model, '_generate_response', fake_generate):
            request.action_send()
        self.assertEqual(request.state, 'done')
        self.assertIn('الجلسة الأولى', captured['prompt'])
        self.assertIn('تقريراً رسمياً', captured['prompt'])
        self.assertNotIn('0551234567', captured['prompt'])
        self.assertNotIn('مذكرة داخلية مقيَّدة', captured['prompt'])

    def test_an_unapproved_agent_still_stops_a_playbook_request(self):
        self.env.ref('era_law_firm_ai.agent_case_assistant').legal_approved = False
        request = self._run('playbook_case_brief')
        request.action_approve()
        with self.assertRaises(UserError):
            request.action_send()
