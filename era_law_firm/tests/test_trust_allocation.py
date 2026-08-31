"""A Case Allocation Transfer has to actually move something.

The type existed, the help text promised it reallocated money between the
client's cases, and it wrote a row that changed no figure: signed_amount was
zero, no journal entry was made, and no per-case balance existed to move. An
accountant would record a transfer, see it in the list, and watch nothing
happen.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTrustAllocation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['res.partner'].create({'name': 'شركة الأفق'})
        cls.other_client = cls.env['res.partner'].create({'name': 'مؤسسة النخبة'})
        cls.account = cls.env['legal.trust.account'].create({
            'partner_id': cls.client.id, 'company_id': cls.env.company.id})
        cls.case_a = cls._make_case(cls, 'قضية أ', cls.client)
        cls.case_b = cls._make_case(cls, 'قضية ب', cls.client)

    def _make_case(self, name, client):
        return self.env['legal.case'].create({
            'name': name, 'client_id': client.id, 'lawyer_id': self.env.user.id,
            'case_type': 'litigation', 'company_id': self.env.company.id,
            'stage_id': self.env.ref('era_law_firm.stage_intake').id,
        })

    def _transaction(self, kind, amount, case=None, destination=None):
        return self.env['legal.trust.transaction'].create({
            'trust_account_id': self.account.id, 'transaction_type': kind,
            'amount': amount, 'case_id': case.id if case else False,
            'destination_case_id': destination.id if destination else False,
        })

    def test_a_deposit_is_earmarked_for_its_case(self):
        self._transaction('deposit', 5000, self.case_a).action_post()
        self.assertEqual(self.case_a.trust_allocated_amount, 5000)
        self.assertEqual(self.case_b.trust_allocated_amount, 0)

    def test_a_transfer_moves_the_allocation_and_leaves_the_total_alone(self):
        self._transaction('deposit', 5000, self.case_a).action_post()
        before = self.account.available_balance
        self._transaction('transfer', 2000, self.case_a, self.case_b).action_post()
        self.assertEqual(self.case_a.trust_allocated_amount, 3000)
        self.assertEqual(self.case_b.trust_allocated_amount, 2000)
        self.assertEqual(self.account.available_balance, before,
                         'the client still holds the same money')

    def test_a_case_cannot_transfer_what_it_never_held(self):
        self._transaction('deposit', 1000, self.case_a).action_post()
        transfer = self._transaction('transfer', 4000, self.case_a, self.case_b)
        with self.assertRaises(UserError):
            transfer.action_post()

    def test_a_transfer_needs_both_ends(self):
        with self.assertRaises(ValidationError):
            self._transaction('transfer', 100, self.case_a)

    def test_a_transfer_to_the_same_case_is_refused(self):
        with self.assertRaises(ValidationError):
            self._transaction('transfer', 100, self.case_a, self.case_a)

    def test_money_cannot_be_earmarked_for_another_client(self):
        """Trust money is the client's; it does not cross to another's matter."""
        stranger = self._make_case('قضية غريبة', self.other_client)
        with self.assertRaises(ValidationError):
            self._transaction('transfer', 100, self.case_a, stranger)

    def test_a_destination_makes_no_sense_on_a_deposit(self):
        with self.assertRaises(ValidationError):
            self._transaction('deposit', 100, self.case_a, self.case_b)

    def test_an_unposted_transfer_moves_nothing(self):
        self._transaction('deposit', 5000, self.case_a).action_post()
        self._transaction('transfer', 2000, self.case_a, self.case_b)
        self.assertEqual(self.case_a.trust_allocated_amount, 5000,
                         'a draft transfer is an intention, not a movement')
