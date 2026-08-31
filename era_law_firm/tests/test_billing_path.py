"""Billing, walked rather than assumed.

Two things were wrong and neither raised anything. A time entry's rate was a
free field nothing filled, so hours reached the invoice at zero and the firm
billed nothing on a document that looked complete. And the invoice wizard
silently discarded any selection that was not yet in a billable state, so an
invoice came out quietly short with no indication of what was missing.
"""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBillingPath(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['res.partner'].create({'name': 'موكّل الفوترة'})
        wizard = cls.env['legal.intake.wizard'].create({
            'client_id': cls.client.id, 'case_type': 'litigation',
            'lawyer_id': cls.env.user.id, 'engagement_type': 'hourly',
            'hourly_rate': 600})
        cls.case = cls.env['legal.case'].browse(wizard.action_open_case()['res_id'])
        cls.engagement = cls.case.engagement_ids[0]

    def _time(self, hours=4, **overrides):
        values = {
            'name': 'مرافعة', 'case_id': self.case.id,
            'engagement_id': self.engagement.id, 'user_id': self.env.user.id,
            'date': fields.Date.today(), 'hours': hours,
        }
        values.update(overrides)
        return self.env['legal.time.entry'].create(values)

    def _invoice(self, **selection):
        wizard = self.env['legal.invoice.create.wizard'].create(dict(
            case_id=self.case.id, engagement_id=self.engagement.id, **selection))
        return self.env['account.move'].browse(
            wizard.action_create_invoice()['res_id'])

    def test_hours_are_billed_at_the_agreed_rate(self):
        """The engagement holds the rate; the entry has to carry it across."""
        entry = self._time(hours=4)
        self.assertEqual(entry.rate, 600, 'a rate of zero invoices nothing')
        self.assertEqual(entry.amount, 2400)

    def test_a_rate_given_by_hand_is_respected(self):
        self.assertEqual(self._time(rate=850).rate, 850)

    def test_the_invoice_carries_the_money(self):
        entry = self._time(hours=4)
        entry.action_mark_billable()
        invoice = self._invoice(time_entry_ids=[(6, 0, entry.ids)])
        self.assertEqual(invoice.amount_untaxed, 2400,
                         'an invoice that looks complete and bills zero is worse '
                         'than one that refuses')

    def test_selecting_a_draft_entry_says_why_it_cannot_be_billed(self):
        """Silently dropping it produces an invoice that is quietly short."""
        entry = self._time()
        with self.assertRaises(UserError) as caught:
            self._invoice(time_entry_ids=[(6, 0, entry.ids)])
        message = str(caught.exception)
        self.assertIn('billable', message, 'it must name the reason, not just refuse')

    def test_selecting_an_unapproved_expense_says_so(self):
        expense = self.env['legal.expense'].create(dict(
            self.env['legal.expense'].default_get(['product_id']),
            name='رسوم', case_id=self.case.id,
            engagement_id=self.engagement.id, amount=300))
        with self.assertRaises(UserError) as caught:
            self._invoice(expense_ids=[(6, 0, expense.ids)])
        self.assertIn('approved', str(caught.exception))

    def test_invoicing_the_same_hours_twice_is_refused_by_name(self):
        entry = self._time()
        entry.action_mark_billable()
        self._invoice(time_entry_ids=[(6, 0, entry.ids)])
        with self.assertRaises(UserError) as caught:
            self._invoice(time_entry_ids=[(6, 0, entry.ids)])
        self.assertIn('already invoiced', str(caught.exception))


@tagged('post_install', '-at_install')
class TestTrustWizard(TransactionCase):
    """The wizard offered a transfer and never asked where to.

    Every transfer through it was refused by the transaction's own constraint —
    an option that could not succeed, presented as if it could.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['res.partner'].create({'name': 'موكّل الأمانة'})
        cls.account = cls.env['legal.trust.account'].create({
            'partner_id': cls.client.id, 'company_id': cls.env.company.id})
        cls.case_a, cls.case_b = (cls._case(cls, 'أ'), cls._case(cls, 'ب'))

    def _case(self, suffix):
        wizard = self.env['legal.intake.wizard'].create({
            'client_id': self.client.id, 'case_type': 'litigation',
            'lawyer_id': self.env.user.id, 'engagement_type': 'none',
            'name': f'قضية {suffix}'})
        return self.env['legal.case'].browse(wizard.action_open_case()['res_id'])

    def _operate(self, **values):
        return self.env['legal.trust.operation.wizard'].create(
            dict(trust_account_id=self.account.id, **values)).action_apply()

    def test_a_transfer_moves_the_earmark(self):
        self._operate(transaction_type='deposit', amount=20000, case_id=self.case_a.id)
        self._operate(transaction_type='transfer', amount=5000,
                      case_id=self.case_a.id, destination_case_id=self.case_b.id)
        self.assertEqual(self.case_a.trust_allocated_amount, 15000)
        self.assertEqual(self.case_b.trust_allocated_amount, 5000)
        self.assertEqual(self.account.available_balance, 20000,
                         'the client still holds the same money')

    def test_a_transfer_with_no_destination_is_refused_clearly(self):
        self._operate(transaction_type='deposit', amount=1000, case_id=self.case_a.id)
        with self.assertRaises(UserError):
            self._operate(transaction_type='transfer', amount=500,
                          case_id=self.case_a.id)

    def test_a_refund_still_needs_a_reason_and_a_reference(self):
        self._operate(transaction_type='deposit', amount=1000, case_id=self.case_a.id)
        with self.assertRaises(UserError):
            self._operate(transaction_type='refund', amount=100, case_id=self.case_a.id)


@tagged('post_install', '-at_install')
class TestBillingRoles(TransactionCase):
    """The sequence as the roles actually run it: the lawyer logs and marks,
    the accountant invoices. The accountant had ACL rows for every billing
    source except time entries — hourly work, the commonest arrangement, was
    the one thing the billing role could not see."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي الفوترة', 'login': 'billing_seq_lawyer',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('era_law_firm.group_legal_lawyer').id])]})
        cls.accountant = cls.env['res.users'].create({
            'name': 'محاسب الفوترة', 'login': 'billing_seq_acct',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('era_law_firm.group_legal_accountant').id,
                                  cls.env.ref('account.group_account_invoice').id])]})
        client = cls.env['res.partner'].create({'name': 'موكّل الأدوار'})
        wizard = cls.env['legal.intake.wizard'].with_user(cls.lawyer).create({
            'client_id': client.id, 'case_type': 'litigation',
            'lawyer_id': cls.lawyer.id, 'engagement_type': 'hourly',
            'hourly_rate': 500})
        cls.case = cls.env['legal.case'].browse(wizard.action_open_case()['res_id'])
        cls.engagement = cls.case.engagement_ids.sudo()
        cls.engagement.action_activate()

    def test_the_lawyer_logs_and_marks_their_own_hours(self):
        entry = self.env['legal.time.entry'].with_user(self.lawyer).create({
            'name': 'مرافعة', 'case_id': self.case.id,
            'engagement_id': self.engagement.id, 'hours': 3,
            'user_id': self.lawyer.id, 'date': fields.Date.today()})
        entry.action_mark_billable()
        self.assertEqual(entry.state, 'billable')
        self.assertEqual(entry.rate, 500, 'the agreed rate came across for them')

    def test_the_accountant_can_actually_invoice_those_hours(self):
        entry = self.env['legal.time.entry'].with_user(self.lawyer).create({
            'name': 'جلسة', 'case_id': self.case.id,
            'engagement_id': self.engagement.id, 'hours': 2,
            'user_id': self.lawyer.id, 'date': fields.Date.today()})
        entry.action_mark_billable()
        action = self.env['legal.invoice.create.wizard'].with_user(self.accountant).create({
            'case_id': self.case.id, 'engagement_id': self.engagement.id,
            'time_entry_ids': [(6, 0, entry.ids)]}).action_create_invoice()
        invoice = self.env['account.move'].browse(action['res_id'])
        self.assertEqual(invoice.amount_untaxed, 1000)
        self.assertEqual(entry.state, 'invoiced')

    def test_the_accountant_still_cannot_log_or_delete_hours(self):
        """Read and write, not create and unlink: hours are the lawyer's act
        and, once logged, evidence."""
        from odoo.exceptions import AccessError
        entry = self.env['legal.time.entry'].with_user(self.lawyer).create({
            'name': 'إثبات', 'case_id': self.case.id,
            'engagement_id': self.engagement.id, 'hours': 1,
            'user_id': self.lawyer.id, 'date': fields.Date.today()})
        with self.assertRaises(AccessError):
            self.env['legal.time.entry'].with_user(self.accountant).create({
                'name': 'دخيل', 'case_id': self.case.id,
                'engagement_id': self.engagement.id, 'hours': 1,
                'user_id': self.accountant.id, 'date': fields.Date.today()})
        with self.assertRaises(AccessError):
            entry.with_user(self.accountant).unlink()
