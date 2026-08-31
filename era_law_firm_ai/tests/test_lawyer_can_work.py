"""The whole flow, run as a lawyer rather than as an administrator.

Everything in this module was exercised as admin, and admin bypasses
field-level groups — so the one bug class that never surfaced was the one a
real user hits first. The identity number is readable by managers only, and
both the party signature and the identity-based conflict matching read it:
a plain lawyer could not open a case at all.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLawyerCanWork(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي الصلاحيات', 'login': 'lawyer_rights_walk',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('era_law_firm.group_legal_lawyer').id,
                cls.env.ref('era_law_firm_ai.group_legal_ai_user').id,
            ])]})
        cls.client = cls.env['res.partner'].create({
            'name': 'موكّل برقم هوية',
            'legal_identity_number': '1088776655'})

    def _open_case(self):
        wizard = self.env['legal.intake.wizard'].with_user(self.lawyer).create({
            'client_id': self.client.id, 'case_type': 'litigation',
            'lawyer_id': self.lawyer.id, 'engagement_type': 'none'})
        return self.env['legal.case'].browse(wizard.action_open_case()['res_id'])

    def test_a_lawyer_can_open_a_case_for_a_client_with_an_id_number(self):
        """The signature and the matching read a field the lawyer cannot."""
        case = self._open_case()
        self.assertEqual(case.state, 'confirmed')

    def test_the_stored_signature_carries_no_identity_number(self):
        """The check is lawyer-readable; the raw number must not survive into it."""
        case = self._open_case()
        signature = case.conflict_check_id.party_signature or ''
        self.assertNotIn('1088776655', signature)
        self.assertEqual(len(signature), 64, 'a sha256 hex digest, nothing readable')

    def test_the_identity_still_counts_in_matching(self):
        """Hiding the number from users must not hide it from the check."""
        duplicate = self.env['res.partner'].create({
            'name': 'اسم مختلف تماماً', 'legal_identity_number': '10-8877-6655'})
        first = self._open_case()
        opponent_case = self.env['legal.case'].with_user(self.lawyer).browse(
            self.env['legal.intake.wizard'].with_user(self.lawyer).create({
                'client_id': self.env['res.partner'].create({'name': 'موكّل آخر'}).id,
                'case_type': 'litigation', 'lawyer_id': self.lawyer.id,
                'engagement_type': 'none',
                'opponent_ids': [(6, 0, duplicate.ids)],
            }).action_open_case()['res_id'])
        # the client of the first case and the opponent of the second share an ID
        self.assertEqual(opponent_case.conflict_check_id.state, 'blocked')
        self.assertIn('identity_number',
                      opponent_case.conflict_check_id.line_ids.mapped('match_basis'))

    def test_the_full_ai_cycle_runs_as_a_lawyer(self):
        case = self._open_case()
        agent = self.env.ref('era_law_firm_ai.agent_research')
        request = self.env['legal.ai.request'].with_user(self.lawyer).create({
            'agent_id': agent.id, 'case_id': case.id,
            'purpose': 'بحث', 'input_payload': 'ما مدة الاعتراض بالاستئناف؟'})
        request.action_approve()

        def fake(agent_self, prompt, chat_history=None, extra_system_context=""):
            return ['السند: نظام المرافعات الشرعية.']
        with patch.object(type(agent), '_generate_response', fake):
            request.action_send()
        self.assertEqual(request.state, 'done')
        self.assertTrue(request.sanitized_response)

    def test_an_empty_payload_is_refused_not_dispatched(self):
        """Ticks may all be empty on this case; consent must not burn on nothing."""
        case = self._open_case()
        request = self.env['legal.ai.request'].with_user(self.lawyer).create({
            'agent_id': self.env.ref('era_law_firm_ai.agent_research').id,
            'case_id': case.id, 'purpose': 'بحث'})
        request.action_approve()
        with self.assertRaises(UserError):
            request.action_send()
