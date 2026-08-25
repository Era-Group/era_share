"""Choosing what to share, without touching a technical field name."""

import base64
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFieldSelection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [
            (4, cls.env.ref('era_law_firm.group_legal_manager').id),
            (4, cls.env.ref('era_law_firm_ai.group_legal_ai_user').id)]
        cls.env.company.legal_ai_enabled = True
        cls.agent = cls.env.ref('era_law_firm_ai.agent_drafting')
        cls.agent.write({
            'legal_processing_location': 'KSA', 'legal_retention_policy': 'None.',
            'legal_approved': True})
        cls.partner = cls.env['res.partner'].create({'name': 'شركة الاختبار'})
        cls.case = cls.env['legal.case'].create({
            'client_id': cls.partner.id, 'case_type': 'litigation',
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id,
            'najiz_number': '4512300987', 'city': 'الرياض',
            'court_id': cls.env.ref('era_law_firm.court_commercial').id})

    def test_the_catalogue_is_in_readable_names(self):
        entries = self.env['legal.ai.field'].search([])
        self.assertTrue(entries)
        for entry in entries:
            self.assertTrue(entry.name)
            self.assertNotEqual(entry.name, entry.technical_name,
                                'the label shown to a lawyer must not be the field name')

    def test_each_agent_ships_with_what_it_needs(self):
        for xmlid in ('agent_contract_review', 'agent_drafting', 'agent_summary', 'agent_research'):
            agent = self.env.ref(f'era_law_firm_ai.{xmlid}')
            self.assertTrue(agent.legal_field_ids, f'{xmlid} has no default selection')

    def test_the_payload_is_built_from_the_ticked_entries_only(self):
        chosen = self.env.ref('era_law_firm_ai.field_case_name') \
            | self.env.ref('era_law_firm_ai.field_case_court')
        request = self.env['legal.ai.request'].create({
            'agent_id': self.agent.id, 'case_id': self.case.id,
            'purpose': 'مسودة', 'field_ids': [(6, 0, chosen.ids)]})
        preview = request.payload_preview
        self.assertIn(self.case.name, preview)
        self.assertIn('المحكمة', preview)
        self.assertNotIn('4512300987', preview, 'an unticked field must not appear')
        self.assertNotIn('الرياض', preview, 'an unticked field must not appear')

    def test_fields_sent_records_what_was_ticked(self):
        chosen = self.env.ref('era_law_firm_ai.field_case_name') \
            | self.env.ref('era_law_firm_ai.field_case_type')
        request = self.env['legal.ai.request'].create({
            'agent_id': self.agent.id, 'case_id': self.case.id,
            'purpose': 'مسودة', 'field_ids': [(6, 0, chosen.ids)]})
        self.assertEqual(request.fields_sent, 'case_type,name')

    def test_the_preview_shows_the_redacted_text_itself(self):
        request = self.env['legal.ai.request'].create({
            'agent_id': self.agent.id, 'case_id': self.case.id, 'purpose': 'مسودة',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)],
            'input_payload': 'الجوال 0551234567 والهوية 1012345678 والبريد a@b.com'})
        preview = request.payload_preview
        self.assertNotIn('0551234567', preview)
        self.assertNotIn('1012345678', preview)
        self.assertNotIn('a@b.com', preview)

    def test_a_saudi_mobile_is_labelled_a_phone_not_an_identity(self):
        """Both are ten digits; the phone rule has to run first."""
        redacted = self.env['legal.ai.request']._redact('جوال 0551234567 وهوية 1012345678')
        self.assertIn('[REDACTED-PHONE]', redacted)
        self.assertIn('[REDACTED-ID]', redacted)
        self.assertNotIn('0551234567', redacted)
        self.assertNotIn('1012345678', redacted)

    def test_an_iban_is_redacted(self):
        redacted = self.env['legal.ai.request']._redact('الآيبان SA4420000001234567891234 للتحويل')
        self.assertNotIn('SA4420000001234567891234', redacted)


@tagged('post_install', '-at_install')
class TestRequestScope(TransactionCase):
    """A request must stay inside one client file."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [
            (4, cls.env.ref('era_law_firm.group_legal_manager').id),
            (4, cls.env.ref('era_law_firm_ai.group_legal_ai_user').id)]
        cls.env.company.legal_ai_enabled = True
        cls.agent = cls.env.ref('era_law_firm_ai.agent_drafting')
        cls.agent.write({'legal_processing_location': 'KSA',
                         'legal_retention_policy': 'None.', 'legal_approved': True})
        stage = cls.env.ref('era_law_firm.stage_intake')
        partner = cls.env['res.partner'].create({'name': 'موكل أ'})
        other_partner = cls.env['res.partner'].create({'name': 'موكل ب'})
        cls.case = cls.env['legal.case'].create({
            'client_id': partner.id, 'case_type': 'litigation', 'stage_id': stage.id})
        cls.other_case = cls.env['legal.case'].create({
            'client_id': other_partner.id, 'case_type': 'litigation', 'stage_id': stage.id})
        cls.document = cls.env['legal.document'].create({
            'name': 'مذكرة', 'case_id': cls.case.id,
            'file_data': base64.b64encode('سر الموكل أ'.encode()), 'file_name': 'memo.txt'})

    def _values(self, **overrides):
        values = {
            'agent_id': self.agent.id, 'purpose': 'اختبار',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)],
        }
        values.update(overrides)
        return values

    def test_a_document_from_another_case_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env['legal.ai.request'].create(self._values(
                case_id=self.other_case.id, document_id=self.document.id))

    def test_a_document_from_the_same_case_is_accepted(self):
        request = self.env['legal.ai.request'].create(self._values(
            case_id=self.case.id, document_id=self.document.id))
        self.assertEqual(request.document_id.case_id, request.case_id)

    def test_moving_the_case_cannot_leave_a_foreign_document_behind(self):
        request = self.env['legal.ai.request'].create(self._values(
            case_id=self.case.id, document_id=self.document.id))
        with self.assertRaises(ValidationError):
            request.case_id = self.other_case

    def test_a_document_alone_is_still_access_checked(self):
        """A request carrying only a document used to skip the check entirely."""
        outsider = self.env['res.users'].create({
            'name': 'محامٍ آخر', 'login': 'ai_scope_outsider',
            'company_id': self.env.company.id,
            'group_ids': [(6, 0, [self.env.ref('era_law_firm.group_legal_lawyer').id,
                                  self.env.ref('era_law_firm_ai.group_legal_ai_user').id,
                                  self.env.ref('base.group_user').id])]})
        request = self.env['legal.ai.request'].create(self._values(document_id=self.document.id))
        request.action_approve()
        with self.assertRaises(UserError):
            request.with_user(outsider)._check_provider_policy()


@tagged('post_install', '-at_install')
class TestClassificationCeiling(TransactionCase):
    """The ceiling is ordered, and a refusal has to say why."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [
            (4, cls.env.ref('era_law_firm.group_legal_manager').id),
            (4, cls.env.ref('era_law_firm_ai.group_legal_ai_user').id)]
        cls.env.company.legal_ai_enabled = True
        cls.agent = cls.env.ref('era_law_firm_ai.agent_summary')
        cls.agent.write({'legal_processing_location': 'KSA',
                         'legal_retention_policy': 'None.', 'legal_approved': True})
        partner = cls.env['res.partner'].create({'name': 'موكل'})
        cls.case = cls.env['legal.case'].create({
            'client_id': partner.id, 'case_type': 'litigation',
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id})
        cls.document = cls.env['legal.document'].create({
            'name': 'مذكرة', 'case_id': cls.case.id,
            'file_data': base64.b64encode(b'text'), 'file_name': 'm.txt'})

    def _request(self):
        return self.env['legal.ai.request'].create({
            'agent_id': self.agent.id, 'case_id': self.case.id,
            'document_id': self.document.id, 'purpose': 'تلخيص',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)]})

    def test_a_new_agent_accepts_confidential_by_default(self):
        """A ceiling that blocks every legal document is a misconfiguration, not a guard."""
        agent = self.env['ai.agent'].create({'name': 'Fresh'})
        self.assertEqual(agent.legal_max_classification, 'confidential')

    def test_the_ceiling_is_inclusive_and_ordered(self):
        request = self._request()
        self.document.ai_classification = 'internal'
        for ceiling, expected in (('public', True), ('internal', False), ('confidential', False)):
            self.agent.legal_max_classification = ceiling
            request.invalidate_recordset()
            self.assertEqual(bool(request._classification_refusal()), expected,
                             f'internal against a {ceiling} ceiling')

    def test_the_refusal_names_both_sides(self):
        self.document.ai_classification = 'confidential'
        self.agent.legal_max_classification = 'internal'
        request = self._request()
        refusal = request._classification_refusal()
        self.assertIn(self.document.name, refusal)
        self.assertIn(self.agent.name, refusal)
        self.assertIn('Confidential', refusal)
        self.assertIn('Internal', refusal)

    def test_a_blocked_document_is_refused_at_any_ceiling(self):
        self.document.ai_classification = 'blocked'
        self.agent.legal_max_classification = 'confidential'
        request = self._request()
        self.assertIn('Blocked', request._classification_refusal())
        request.action_approve()
        with self.assertRaises(UserError):
            request.action_send()

    def test_the_conflict_shows_before_sending(self):
        self.document.ai_classification = 'confidential'
        self.agent.legal_max_classification = 'internal'
        self.assertTrue(self._request().classification_blocked)
        self.agent.legal_max_classification = 'confidential'
        self.assertFalse(self._request().classification_blocked)


@tagged('post_install', '-at_install')
class TestCaseAIActivity(TransactionCase):
    """A case reports the AI work done on it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [
            (4, cls.env.ref('era_law_firm.group_legal_manager').id),
            (4, cls.env.ref('era_law_firm_ai.group_legal_ai_user').id)]
        cls.env.company.legal_ai_enabled = True
        cls.agent = cls.env.ref('era_law_firm_ai.agent_summary')
        cls.agent.write({'legal_processing_location': 'KSA', 'legal_retention_policy': 'None.',
                         'legal_max_classification': 'confidential', 'legal_approved': True})
        partner = cls.env['res.partner'].create({'name': 'موكل'})
        cls.case = cls.env['legal.case'].create({
            'client_id': partner.id, 'case_type': 'litigation',
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id})
        cls.document = cls.env['legal.document'].create({
            'name': 'مذكرة', 'case_id': cls.case.id,
            'file_data': base64.b64encode(b'text'), 'file_name': 'm.txt'})

    def _request(self, **overrides):
        values = {
            'agent_id': self.agent.id, 'purpose': 'تلخيص',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)],
        }
        values.update(overrides)
        return self.env['legal.ai.request'].create(values)

    def test_a_case_with_no_ai_work_counts_zero(self):
        self.assertEqual(self.case.ai_request_count, 0)
        self.assertEqual(self.case.ai_dispatched_count, 0)

    def test_requests_are_counted_on_their_case(self):
        self._request(case_id=self.case.id)
        self._request(case_id=self.case.id)
        self.case.invalidate_recordset()
        self.assertEqual(self.case.ai_request_count, 2)

    def test_a_request_raised_on_a_document_alone_still_counts(self):
        """Otherwise the case would under-report what was sent about it."""
        request = self._request(document_id=self.document.id)
        self.assertEqual(request.case_id, self.case)
        self.case.invalidate_recordset()
        self.assertEqual(self.case.ai_request_count, 1)

    def test_drafts_are_counted_separately_from_what_was_sent(self):
        """A draft never sent shared nothing, and the two must not read alike."""
        self._request(case_id=self.case.id)
        sent = self._request(case_id=self.case.id)
        sent.action_approve()
        with patch.object(type(self.agent), '_generate_response',
                          lambda *a, **k: ['ملخص']):
            sent.action_send()
        self.case.invalidate_recordset()
        self.assertEqual(self.case.ai_request_count, 2)
        self.assertEqual(self.case.ai_dispatched_count, 1)

    def test_the_button_opens_only_this_case(self):
        action = self.case.action_view_ai_requests()
        self.assertEqual(action['res_model'], 'legal.ai.request')
        self.assertIn(('case_id', '=', self.case.id), action['domain'])
