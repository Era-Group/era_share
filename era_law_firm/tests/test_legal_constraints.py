"""The four constraints the plan's acceptance criteria require to be covered:
no negative trust balance, no double billing, company isolation, and hiding
restricted data from users who may not see it.
"""

import base64

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged

from .common import LegalCommon


@tagged('post_install', '-at_install')
class TestTrustBalance(LegalCommon):
    """Client money is a liability. It must never go negative, by any route."""

    def test_refund_beyond_balance_is_refused(self):
        trust = self._trust_account()
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'deposit', 'amount': 1000}).action_apply()
        with self.assertRaises(UserError):
            self.env['legal.trust.operation.wizard'].create({
                'trust_account_id': trust.id, 'transaction_type': 'refund', 'amount': 2000,
                'reference': 'X', 'reason': 'Too much'}).action_apply()
        trust.invalidate_recordset()
        self.assertEqual(trust.available_balance, 1000)

    def test_two_withdrawals_cannot_jointly_overdraw(self):
        trust = self._trust_account()
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'deposit', 'amount': 1000}).action_apply()
        first = self.env['legal.trust.transaction'].create({
            'trust_account_id': trust.id, 'transaction_type': 'refund', 'amount': 700,
            'reference': 'A', 'reason': 'first'})
        second = self.env['legal.trust.transaction'].create({
            'trust_account_id': trust.id, 'transaction_type': 'refund', 'amount': 700,
            'reference': 'B', 'reason': 'second'})
        first.action_post()
        with self.assertRaises(UserError):
            second.action_post()
        trust.invalidate_recordset()
        self.assertEqual(trust.available_balance, 300)

    def test_amount_must_be_positive(self):
        trust = self._trust_account()
        for bad in (0, -100):
            with self.assertRaises(ValidationError):
                self.env['legal.trust.transaction'].create({
                    'trust_account_id': trust.id, 'transaction_type': 'deposit', 'amount': bad})

    def test_one_trust_account_per_client_and_company(self):
        self._trust_account()
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._trust_account()

    def test_deposit_is_a_liability_not_revenue(self):
        trust = self._trust_account()
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'deposit', 'amount': 5000}).action_apply()
        move = trust.transaction_ids.move_id
        self.assertTrue(move, 'a deposit must produce a journal entry')
        credited = move.line_ids.filtered(lambda line: line.credit)
        self.assertEqual(credited.account_id, self.acc_liability)
        self.assertNotEqual(credited.account_id.account_type, 'income')

    def test_posted_transaction_is_reversed_never_deleted(self):
        trust = self._trust_account()
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'deposit', 'amount': 3000}).action_apply()
        transaction = trust.transaction_ids
        with self.assertRaises(UserError):
            transaction.unlink()
        transaction.action_cancel()
        self.assertEqual(transaction.state, 'cancelled')
        self.assertTrue(transaction.reversal_move_id, 'cancelling must leave a reversing entry')

    def test_account_cannot_close_over_a_balance(self):
        trust = self._trust_account()
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'deposit', 'amount': 100}).action_apply()
        trust.invalidate_recordset()
        with self.assertRaises(UserError):
            trust.action_close()


@tagged('post_install', '-at_install')
class TestDoubleBilling(LegalCommon):
    """A source of value may be invoiced exactly once."""

    def setUp(self):
        super().setUp()
        self.case = self._confirmed_case()
        self.engagement = self._active_engagement(self.case)
        self.entry = self.env['legal.time.entry'].create({
            'name': 'Hearing attendance', 'case_id': self.case.id,
            'engagement_id': self.engagement.id, 'user_id': self.lawyer.id, 'hours': 5, 'rate': 800})
        self.entry.action_mark_billable()

    def _invoice(self, records):
        return self.env['legal.invoice.create.wizard'].create({
            'case_id': self.case.id, 'engagement_id': self.engagement.id,
            'time_entry_ids': [(6, 0, records.ids)]}).action_create_invoice()

    def test_a_time_entry_cannot_be_invoiced_twice(self):
        self._invoice(self.entry)
        self.assertEqual(self.entry.state, 'invoiced')
        with self.assertRaises(UserError):
            self._invoice(self.entry)

    def test_invoicing_nothing_is_refused(self):
        draft = self.env['legal.time.entry'].create({
            'name': 'Not yet billable', 'case_id': self.case.id,
            'engagement_id': self.engagement.id, 'user_id': self.lawyer.id, 'hours': 2, 'rate': 800})
        with self.assertRaises(UserError):
            self._invoice(draft)

    def test_an_inactive_engagement_cannot_be_invoiced(self):
        self.engagement.action_close()
        with self.assertRaises(UserError):
            self._invoice(self.entry)

    def test_expense_cannot_be_invoiced_twice(self):
        expense = self.env['legal.expense'].create({
            'name': 'Court fee', 'case_id': self.case.id, 'engagement_id': self.engagement.id,
            'amount': 1500, 'product_id': self.product.id})
        expense.action_approve()
        wizard = self.env['legal.invoice.create.wizard'].create({
            'case_id': self.case.id, 'engagement_id': self.engagement.id,
            'expense_ids': [(6, 0, expense.ids)]})
        wizard.action_create_invoice()
        self.assertEqual(expense.state, 'invoiced')
        with self.assertRaises(UserError):
            self.env['legal.invoice.create.wizard'].create({
                'case_id': self.case.id, 'engagement_id': self.engagement.id,
                'expense_ids': [(6, 0, expense.ids)]}).action_create_invoice()


@tagged('post_install', '-at_install')
class TestCompanyIsolation(LegalCommon):
    """A legal file must never be visible outside the company that owns it."""

    def setUp(self):
        super().setUp()
        self.other_company = self.env['res.company'].create({'name': 'Second Firm'})
        self.own_user = self.env['res.users'].create({
            'name': 'Own Firm Manager', 'login': 'legal_test_own',
            'company_id': self.company.id, 'company_ids': [(6, 0, [self.company.id])],
            'group_ids': [(4, self.env.ref('era_law_firm.group_legal_manager').id)]})

    def test_a_case_in_another_company_is_invisible(self):
        foreign = self.env['legal.case'].create({
            'client_id': self.client.id, 'lawyer_id': self.lawyer.id, 'case_type': 'litigation',
            'stage_id': self.env.ref('era_law_firm.stage_intake').id,
            'company_id': self.other_company.id})
        self.assertNotIn(foreign, self.env['legal.case'].with_user(self.own_user).search([]))
        with self.assertRaises(AccessError):
            foreign.with_user(self.own_user).read(['name'])

    def test_a_case_in_the_own_company_is_visible(self):
        own = self._make_case()
        self.assertIn(own, self.env['legal.case'].with_user(self.own_user).search([]))

    def test_company_cannot_move_once_the_file_has_content(self):
        case = self._confirmed_case()
        self.env['legal.hearing'].create({
            'name': 'Session', 'case_id': case.id, 'lawyer_id': self.lawyer.id,
            'start_datetime': fields.Datetime.now(),
            'stop_datetime': fields.Datetime.add(fields.Datetime.now(), hours=1)})
        with self.assertRaises(UserError):
            case.write({'company_id': self.other_company.id})


@tagged('post_install', '-at_install')
class TestRestrictedData(LegalCommon):
    """Identity numbers and restricted documents are hidden from staff without the right."""

    def setUp(self):
        super().setUp()
        self.staff = self.env['res.users'].create({
            'name': 'Legal Assistant', 'login': 'legal_test_staff',
            'company_id': self.company.id, 'company_ids': [(6, 0, [self.company.id])],
            'group_ids': [(6, 0, [self.env.ref('era_law_firm.group_legal_staff').id,
                                  self.env.ref('base.group_user').id])]})

    def test_identity_number_is_manager_only(self):
        self.client.legal_identity_number = '1012345678'
        with self.assertRaises(AccessError):
            self.client.with_user(self.staff).read(['legal_identity_number'])
        self.assertEqual(
            self.client.with_user(self.env.user).read(['legal_identity_number'])[0]['legal_identity_number'],
            '1012345678')

    def test_a_restricted_document_is_hidden_from_uninvolved_staff(self):
        case = self._confirmed_case()
        restricted = self.env['legal.document'].create({
            'name': 'Privileged Memo', 'case_id': case.id, 'owner_id': self.lawyer.id,
            'restricted': True,
            'file_data': base64.b64encode(b'privileged'), 'file_name': 'memo.txt'})
        self.assertNotIn(restricted, self.env['legal.document'].with_user(self.staff).search([]))

        allowed = self.env['legal.document'].create({
            'name': 'Open Filing', 'case_id': case.id, 'owner_id': self.lawyer.id,
            'restricted': False,
            'file_data': base64.b64encode(b'open'), 'file_name': 'open.txt'})
        self.assertIn(allowed, self.env['legal.document'].with_user(self.staff).search([]))

    def test_audit_log_cannot_be_altered_or_erased(self):
        log = self.env['legal.audit.log'].create({
            'model_name': 'res.partner', 'res_id': self.client.id, 'operation': 'test'})
        with self.assertRaises(AccessError):
            log.write({'operation': 'tampered'})
        with self.assertRaises(AccessError):
            log.unlink()
