"""End-to-end walk of the intake-to-close path, exercised through the UI actions.

This is the regression guard for the whole product: every step here is a step a
user performs from a button, so a break anywhere in the chain fails loudly.
"""

import base64

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import LegalCommon


@tagged('post_install', '-at_install')
class TestLegalWorkflow(LegalCommon):

    def test_case_reference_is_sequenced_per_company(self):
        case = self._make_case()
        self.assertTrue(case.name.startswith('LAW/'), case.name)
        self.assertNotEqual(self._make_case().name, case.name)

    def test_party_signature_survives_partner_without_phone(self):
        """res.partner lost `mobile` in Odoo 19; the signature must not reach for it."""
        case = self._make_case()
        self.assertIsInstance(case._party_signature(), str)

    def test_conflict_check_blocks_and_clears(self):
        case = self._make_case()
        self.env['legal.case.party'].create({
            'case_id': case.id, 'partner_id': self.opponent.id, 'role': 'opponent'})
        case.action_run_conflict_check()
        self.assertEqual(case.conflict_check_id.state, 'clear')

        # a second case sharing the opponent must now be flagged
        other = self._make_case()
        self.env['legal.case.party'].create({
            'case_id': other.id, 'partner_id': self.opponent.id, 'role': 'opponent'})
        case.action_confirm()
        other.action_run_conflict_check()
        self.assertEqual(other.conflict_check_id.state, 'blocked')
        self.assertTrue(other.conflict_check_id.line_ids)

    def test_confirm_requires_a_current_conflict_check(self):
        case = self._make_case()
        with self.assertRaises(UserError):
            case.action_confirm()

    def test_changing_parties_voids_the_conflict_check(self):
        case = self._confirmed_case()
        self.env['legal.case.party'].create({
            'case_id': case.id, 'partner_id': self.env['res.partner'].create({'name': 'Late Party'}).id,
            'role': 'other'})
        self.assertFalse(case.conflict_check_id, 'a new party must void the previous check')

    def test_hearing_syncs_to_the_calendar(self):
        case = self._confirmed_case()
        hearing = self.env['legal.hearing'].create({
            'name': 'First Session', 'case_id': case.id, 'lawyer_id': self.lawyer.id,
            'start_datetime': fields.Datetime.add(fields.Datetime.now(), days=7),
            'stop_datetime': fields.Datetime.add(fields.Datetime.now(), days=7, hours=1)})
        hearing.action_confirm()
        self.assertTrue(hearing.calendar_event_id)
        hearing.action_cancel()
        self.assertFalse(hearing.calendar_event_id)

    def test_deadline_rule_suggests_but_does_not_bind(self):
        case = self._confirmed_case()
        deadline = self.env['legal.deadline'].create({
            'name': 'Appeal window', 'case_id': case.id, 'user_id': self.lawyer.id,
            'deadline_date': fields.Date.today(), 'source': 'Judgment served',
            'rule_id': self.env.ref('era_law_firm.deadline_rule_appeal_30').id,
            'start_date': fields.Date.today()})
        self.assertEqual(deadline.suggested_date, fields.Date.add(fields.Date.today(), days=30))
        self.assertNotEqual(deadline.deadline_date, deadline.suggested_date,
                            'the suggestion must not silently become the binding date')
        deadline.action_adopt_suggested_date()
        self.assertEqual(deadline.deadline_date, deadline.suggested_date)

    def test_document_upload_review_and_publication(self):
        case = self._confirmed_case()
        document = self.env['legal.document'].create({
            'name': 'Statement of Claim', 'case_id': case.id, 'document_type': 'pleading',
            'owner_id': self.lawyer.id,
            'file_data': base64.b64encode(b'%PDF-1.4 statement of claim'),
            'file_name': 'claim.pdf'})
        self.assertTrue(document.attachment_id, 'the binary must create the attachment')
        self.assertEqual(document.attachment_id.raw, b'%PDF-1.4 statement of claim')

        with self.assertRaises(UserError, msg='an unapproved document must not be publishable'):
            document.action_publish_portal()

        document.action_submit_review()
        document.action_approve()
        self.assertEqual(document.state, 'approved')
        document.action_publish_portal()
        self.assertTrue(document.portal_published)

    def test_author_cannot_approve_their_own_document(self):
        case = self._confirmed_case()
        document = self.env['legal.document'].create({
            'name': 'Own Draft', 'case_id': case.id, 'owner_id': self.env.user.id,
            'file_data': base64.b64encode(b'draft'), 'file_name': 'draft.txt'})
        document.action_submit_review()
        with self.assertRaises(UserError):
            document.action_approve()

    def test_full_intake_to_close(self):
        case = self._confirmed_case()
        engagement = self._active_engagement(case)

        entry = self.env['legal.time.entry'].create({
            'name': 'Drafting statement of claim', 'case_id': case.id,
            'engagement_id': engagement.id, 'user_id': self.lawyer.id, 'hours': 10, 'rate': 800})
        entry.action_mark_billable()

        trust = self._trust_account()
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'deposit',
            'amount': 50000, 'reference': 'Bank transfer 998'}).action_apply()
        trust.invalidate_recordset()
        self.assertEqual(trust.available_balance, 50000)

        wizard_action = case.action_open_invoice_wizard()
        self.assertEqual(wizard_action['res_model'], 'legal.invoice.create.wizard')
        result = self.env['legal.invoice.create.wizard'].create({
            'case_id': case.id, 'engagement_id': engagement.id,
            'time_entry_ids': [(6, 0, entry.ids)]}).action_create_invoice()

        invoice = self.env['account.move'].browse(result['res_id'])
        self.assertEqual(invoice.legal_case_id, case)
        self.assertEqual(entry.state, 'invoiced')
        invoice.write({'invoice_date': fields.Date.today()})
        invoice.action_post()
        self.assertEqual(invoice.amount_total, 8000)

        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'apply', 'amount': 8000,
            'invoice_id': invoice.id, 'case_id': case.id}).action_apply()
        trust.invalidate_recordset()
        self.assertEqual(trust.available_balance, 42000)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))

        case.invalidate_recordset()
        self.assertEqual(case.invoiced_amount, 8000)
        self.assertEqual(case.billable_hours, 10)

        with self.assertRaises(UserError, msg='a case must not close over client money'):
            case.action_close()

        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'refund', 'amount': 42000,
            'reference': 'Refund transfer 1002', 'reason': 'Case closed'}).action_apply()
        trust.invalidate_recordset()
        case.action_close()
        self.assertEqual(case.state, 'closed')
        self.assertTrue(case.close_date)

    def test_cancelling_an_invoice_releases_its_sources(self):
        case = self._confirmed_case()
        engagement = self._active_engagement(case)
        entry = self.env['legal.time.entry'].create({
            'name': 'Research', 'case_id': case.id, 'engagement_id': engagement.id,
            'user_id': self.lawyer.id, 'hours': 3, 'rate': 800})
        entry.action_mark_billable()
        result = self.env['legal.invoice.create.wizard'].create({
            'case_id': case.id, 'engagement_id': engagement.id,
            'time_entry_ids': [(6, 0, entry.ids)]}).action_create_invoice()
        invoice = self.env['account.move'].browse(result['res_id'])
        invoice.write({'invoice_date': fields.Date.today()})
        invoice.action_post()
        invoice.button_cancel()
        self.assertEqual(entry.state, 'billable')
        self.assertFalse(entry.invoice_line_id)
