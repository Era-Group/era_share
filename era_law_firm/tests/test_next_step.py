"""The guidance and the gates must never disagree.

A banner that says "confirm the case" while the confirm button refuses is
worse than no banner: it moves the confusion one step later and adds a reason
to distrust everything else on the screen. Both read the same rules, and these
tests are what keeps that true.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNextStep(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['res.partner'].create({'name': 'موكّل الإرشاد'})
        cls.case = cls.env['legal.case'].create({
            'name': 'قضية', 'client_id': cls.client.id, 'lawyer_id': cls.env.user.id,
            'case_type': 'litigation', 'company_id': cls.env.company.id,
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id})

    def _titles(self, case=None):
        return [title for title, _why in (case or self.case)._next_step_items()]

    def _check(self):
        check = self.env['legal.conflict.check'].create({
            'case_id': self.case.id, 'company_id': self.env.company.id})
        check.action_run_check()
        self.case.invalidate_recordset()
        return check

    def test_a_new_case_is_told_to_run_a_conflict_check(self):
        self.assertTrue(any('conflict' in t.lower() for t in self._titles()))

    def test_what_it_asks_for_is_what_confirming_refuses_without(self):
        """The banner and the gate, read against each other."""
        with self.assertRaises(UserError):
            self.case.action_confirm()
        self.assertTrue(self._titles(), 'refused, so something must be pending')

    def test_a_cleared_check_turns_the_advice_to_confirm(self):
        self._check()
        self.assertTrue(any('confirm' in t.lower() for t in self._titles()))

    def test_the_advice_is_true_the_case_really_confirms(self):
        self._check()
        self.case.action_confirm()
        self.assertEqual(self.case.state, 'confirmed')

    def test_adding_a_party_sends_the_case_back_for_a_check(self):
        """Adding a party clears the link, so a fresh check is genuinely due."""
        self._check()
        self.assertTrue(any('confirm' in t.lower() for t in self._titles()))
        self.env['legal.case.party'].create({
            'case_id': self.case.id, 'role': 'opponent',
            'partner_id': self.env['res.partner'].create({'name': 'خصم'}).id,
            'company_id': self.env.company.id})
        self.case.invalidate_recordset()
        self.assertFalse(self.case.conflict_check_id, 'the old result no longer applies')
        self.assertTrue(any('conflict' in t.lower() for t in self._titles()))
        with self.assertRaises(UserError):
            self.case.action_confirm()

    def test_a_stale_check_is_called_out_rather_than_trusted(self):
        """The safety net behind the gate, tested as one.

        Every ordinary edit clears the link, so this state is not reachable by
        hand. action_confirm still compares the signature, and the banner has
        to agree with it if the state ever does arise.
        """
        check = self._check()
        # Reattach the result after the file has moved on beneath it.
        self.case.client_id = self.env['res.partner'].create({'name': 'موكّل آخر'})
        self.case.conflict_check_id = check
        self.case.invalidate_recordset()
        self.assertNotEqual(check.party_signature, self.case._party_signature())
        self.assertTrue(any('re-run' in t.lower() for t in self._titles()))
        with self.assertRaises(UserError):
            self.case.action_confirm()

    def test_a_confirmed_case_is_told_to_open_an_engagement(self):
        self._check()
        self.case.action_confirm()
        self.case.invalidate_recordset()
        self.assertTrue(any('engagement' in t.lower() for t in self._titles()))

    def test_a_draft_engagement_is_not_mistaken_for_an_active_one(self):
        self._check()
        self.case.action_confirm()
        self.env['legal.engagement'].create({
            'case_id': self.case.id, 'name': 'أتعاب', 'billing_type': 'hourly',
            'hourly_rate': 400})
        self.case.invalidate_recordset()
        self.assertTrue(any('activate' in t.lower() for t in self._titles()))

    def test_money_held_for_the_case_is_named_before_closing(self):
        """The close gate refuses over trust; the banner says so first."""
        self._check()
        self.case.action_confirm()
        account = self.env['legal.trust.account'].create({
            'partner_id': self.client.id, 'company_id': self.env.company.id})
        self.env['legal.trust.transaction'].create({
            'trust_account_id': account.id, 'transaction_type': 'deposit',
            'amount': 2500, 'case_id': self.case.id}).action_post()
        self.case.invalidate_recordset()
        self.assertTrue(any('2500' in t or 'held' in t.lower() for t in self._titles()))
        with self.assertRaises(UserError):
            self.case.action_close()

    def test_a_closed_case_asks_for_nothing(self):
        self._check()
        self.case.action_confirm()
        self.case.action_close()
        self.case.invalidate_recordset()
        self.assertTrue(any('closed' in t.lower() for t in self._titles()))
