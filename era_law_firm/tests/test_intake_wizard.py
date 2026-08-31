"""Opening a file has an order, and the form never taught it.

Create the case, remember the parties, remember the check, discover on
confirming that one was missed. Every step is guarded and none is offered.
The wizard runs them in the order the rules require — without becoming a way
around them.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestIntakeWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['res.partner'].create({'name': 'موكّل'})
        cls.opponent = cls.env['res.partner'].create({'name': 'خصم'})

    def _wizard(self, **overrides):
        values = {
            'client_id': self.client.id, 'case_type': 'litigation',
            'lawyer_id': self.env.user.id, 'engagement_type': 'none',
        }
        values.update(overrides)
        return self.env['legal.intake.wizard'].create(values)

    def _open(self, wizard):
        return self.env['legal.case'].browse(wizard.action_open_case()['res_id'])

    def _existing_case_against(self, partner):
        case = self.env['legal.case'].create({
            'name': 'قضية سابقة', 'client_id': self.client.id,
            'lawyer_id': self.env.user.id, 'case_type': 'litigation',
            'company_id': self.env.company.id,
            'stage_id': self.env.ref('era_law_firm.stage_intake').id})
        self.env['legal.case.party'].create({
            'case_id': case.id, 'partner_id': partner.id, 'role': 'opponent',
            'company_id': self.env.company.id})
        check = self.env['legal.conflict.check'].create({
            'case_id': case.id, 'company_id': self.env.company.id})
        check.action_run_check()
        case.action_confirm()
        return case

    def test_it_does_every_step_in_the_order_the_rules_need(self):
        case = self._open(self._wizard(
            name='نزاع', opponent_ids=[(6, 0, self.opponent.ids)]))
        self.assertEqual(case.party_ids.partner_id, self.opponent,
                         'the parties exist before the check runs')
        self.assertEqual(case.conflict_check_id.state, 'clear')
        self.assertEqual(case.state, 'confirmed')

    def test_a_conflict_opens_the_file_but_does_not_confirm_it(self):
        """It must not become a way past the gate it is meant to explain."""
        self._existing_case_against(self.opponent)
        case = self._open(self._wizard(opponent_ids=[(6, 0, self.opponent.ids)]))
        self.assertEqual(case.conflict_check_id.state, 'blocked')
        self.assertEqual(case.state, 'draft', 'a manager still has to decide')
        with self.assertRaises(UserError):
            case.action_confirm()

    def test_the_preview_finds_it_before_the_case_exists(self):
        self._existing_case_against(self.opponent)
        wizard = self._wizard(opponent_ids=[(6, 0, self.opponent.ids)])
        self.assertIn('share a party', wizard.conflict_preview)
        self.assertIn(self.opponent.name, wizard.conflict_preview)

    def test_nothing_found_reads_differently_from_nothing_to_look_for(self):
        """Showing 'name the parties' when they are named reads as not having run."""
        unnamed = self._wizard()
        self.assertIn('Name the opposing parties', unnamed.conflict_preview)
        named = self._wizard(opponent_ids=[(6, 0, self.opponent.ids)])
        self.assertIn('No existing file', named.conflict_preview)

    def test_an_hourly_engagement_arrives_active(self):
        case = self._open(self._wizard(engagement_type='hourly', hourly_rate=750))
        engagement = case.engagement_ids
        self.assertEqual(engagement.state, 'active')
        self.assertEqual(engagement.hourly_rate, 750)
        self.assertTrue(engagement.product_id, 'billing product filled in for them')

    def test_a_fixed_fee_engagement_carries_its_amount(self):
        case = self._open(self._wizard(engagement_type='fixed', fixed_amount=25000))
        self.assertEqual(case.engagement_ids.billing_type, 'fixed')
        self.assertEqual(case.engagement_ids.amount, 25000)

    def test_a_rate_is_required_before_anything_is_created(self):
        """Refuse early rather than leave a half-opened file behind."""
        before = self.env['legal.case'].search_count([])
        with self.assertRaises(UserError):
            self._wizard(engagement_type='hourly').action_open_case()
        self.assertEqual(self.env['legal.case'].search_count([]), before)

    def test_no_fee_arrangement_yet_is_a_valid_answer(self):
        case = self._open(self._wizard(engagement_type='none'))
        self.assertFalse(case.engagement_ids)
        self.assertEqual(case.state, 'confirmed')
