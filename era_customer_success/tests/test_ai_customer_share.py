from unittest.mock import patch

from odoo.exceptions import UserError
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAiCustomerShare(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({
            'name': 'AI Share Test Customer',
            'is_company': True,
        })
        cls.account = cls.env['cs.account'].create({
            'partner_id': partner.id,
            'company_id': cls.env.company.id,
            'csm_user_id': False,
        })
        cls.share = cls.env['cs.ai.customer.share'].create({
            'name': 'Review Table',
            'request_description': 'Name, adoption and customer voice',
            'account_ids': [(6, 0, cls.account.ids)],
        })

    def test_only_allowed_fields_are_selected(self):
        selected = self.share._filter_allowed_fields([
            'customer_name', 'revenue', 'customer_voice', 'customer_name'])
        self.assertEqual(selected, ['customer_name', 'customer_voice'])

    def test_percentages_are_clamped_and_tolerate_percent_sign(self):
        self.assertEqual(self.share._safe_percent('82.5%'), 82.5)
        self.assertEqual(self.share._safe_percent(140), 100.0)
        self.assertEqual(self.share._safe_percent('missing', 45), 45.0)

    def test_selected_text_fields_never_remain_blank(self):
        values = {'last_contact': '', 'contact_result': '', 'customer_voice': ''}
        selected = ['last_contact', 'contact_result', 'customer_voice']
        self.share._complete_missing_values(values, self.account, selected)
        self.assertTrue(values['last_contact'])
        self.assertTrue(values['contact_result'])
        self.assertTrue(values['customer_voice'])
        self.assertTrue(self.share._is_bilingual_text(values['contact_result']))
        self.assertTrue(self.share._is_bilingual_text(values['customer_voice']))

    def test_monolingual_ai_text_is_rejected_as_incomplete(self):
        values = {
            'contact_result': 'تم التواصل مع العميل',
            'customer_voice': 'Positive customer feedback',
        }
        missing = self.share._missing_selected_values(
            values, ['contact_result', 'customer_voice'])
        self.assertEqual(set(missing), {'contact_result', 'customer_voice'})

    def test_bilingual_portal_text_uses_newline_instead_of_slash(self):
        value = self.share.format_bilingual_text(
            'تم التواصل مع العميل. / Customer contact was completed.')
        self.assertEqual(
            value, 'تم التواصل مع العميل.\nCustomer contact was completed.')

    def test_module_evidence_keeps_exact_allowed_options(self):
        options = ['Sales', 'Accounting', 'Inventory']
        evidence = self.account._build_sheet_module_evidence(options)
        self.assertEqual(evidence['allowed_module_options'], options)
        self.assertIn('support_ticket_subjects', evidence)
        self.assertIn('sold_or_subscribed_products', evidence)

    def test_approved_table_is_revoked_when_a_row_changes(self):
        line = self.env['cs.ai.customer.share.line'].create({
            'share_id': self.share.id,
            'account_id': self.account.id,
            'customer_name': self.account.partner_id.name,
        })
        self.share.write({'state': 'approved'})
        line.contact_result = 'Reviewed result'
        self.assertEqual(self.share.state, 'prepared')

    def test_flagged_customers_are_selected_by_default(self):
        self.account.sudo().send_to_portal_share = True
        defaults = self.env['cs.ai.customer.share'].default_get(['account_ids'])
        command = defaults['account_ids'][0]
        self.assertIn(self.account.id, command[2])

    def test_prepare_button_queues_background_work_without_calling_ai(self):
        with patch.object(type(self.share), '_analyse_requested_fields') as analyse, \
                patch.object(type(self.share), '_trigger_preparation_cron') as trigger:
            action = self.share.action_prepare_with_ai()

        analyse.assert_not_called()
        trigger.assert_called_once()
        self.assertEqual(self.share.preparation_state, 'queued')
        self.assertEqual(self.share.preparation_total, 1)
        self.assertEqual(self.share.preparation_progress, 0)
        self.assertEqual(action['tag'], 'display_notification')

    def test_portal_cannot_publish_before_review_approval(self):
        with self.assertRaisesRegex(Exception, 'Approve the reviewed AI table'):
            self.share.action_publish_portal()

    def test_editing_approved_row_unpublishes_portal(self):
        line = self.env['cs.ai.customer.share.line'].create({
            'share_id': self.share.id,
            'account_id': self.account.id,
            'customer_name': self.account.partner_id.name,
        })
        self.share.write({
            'state': 'approved',
            'portal_enabled': True,
        })
        line.customer_voice = 'Reviewed voice'
        self.assertFalse(self.share.portal_enabled)
        self.assertEqual(self.share.state, 'prepared')

    def test_deleting_approved_row_unpublishes_portal(self):
        line = self.env['cs.ai.customer.share.line'].create({
            'share_id': self.share.id,
            'account_id': self.account.id,
            'customer_name': self.account.partner_id.name,
        })
        self.share.write({'state': 'approved', 'portal_enabled': True})
        line.unlink()
        self.assertFalse(self.share.portal_enabled)
        self.assertEqual(self.share.state, 'draft')

    def test_scheduled_refresh_keeps_approval_and_portal_enabled(self):
        self.env['cs.ai.customer.share.line'].create({
            'share_id': self.share.id,
            'account_id': self.account.id,
            'customer_name': 'Old Name',
        })
        self.share.write({
            'state': 'approved',
            'portal_enabled': True,
            'selected_fields': 'customer_name',
            'include_customer_name': True,
        })
        generated = {
            'share_id': self.share.id,
            'account_id': self.account.id,
            'customer_name': 'Refreshed Name',
        }
        with patch.object(type(self.share), '_prepare_account_row', return_value=generated):
            self.share._refresh_approved_portal_table()
        self.assertEqual(self.share.state, 'approved')
        self.assertTrue(self.share.portal_enabled)
        self.assertEqual(self.share.line_ids.customer_name, 'Refreshed Name')
        self.assertTrue(self.share.last_auto_refresh_on)

    def test_ai_failure_preserves_existing_published_rows(self):
        line = self.env['cs.ai.customer.share.line'].create({
            'share_id': self.share.id,
            'account_id': self.account.id,
            'customer_name': 'Published Name',
        })
        self.share.write({
            'state': 'approved',
            'portal_enabled': True,
            'selected_fields': 'customer_name',
            'include_customer_name': True,
        })
        with patch.object(type(self.share), '_prepare_account_row',
                          side_effect=UserError('AI unavailable')):
            with self.assertRaises(UserError):
                self.share._refresh_approved_portal_table()
        self.assertTrue(line.exists())
        self.assertEqual(self.share.line_ids.customer_name, 'Published Name')

    def test_weekly_refresh_excludes_customers_not_marked_for_portal(self):
        line = self.env['cs.ai.customer.share.line'].create({
            'share_id': self.share.id,
            'account_id': self.account.id,
            'customer_name': 'Previously Published',
        })
        self.account.sudo().send_to_portal_share = False
        self.share.write({
            'state': 'approved',
            'portal_enabled': True,
            'selected_fields': 'customer_name',
            'include_customer_name': True,
        })
        with patch.object(type(self.share), '_prepare_account_row') as prepare:
            self.share._refresh_approved_portal_table(respect_portal_selection=True)
        prepare.assert_not_called()
        self.assertFalse(line.exists())
        self.assertFalse(self.share.line_ids)
        self.assertEqual(self.share.state, 'approved')
        self.assertTrue(self.share.portal_enabled)

    def test_weekly_cron_refreshes_only_live_approved_tables(self):
        self.share.write({'state': 'approved', 'portal_enabled': True})
        draft = self.env['cs.ai.customer.share'].create({
            'name': 'Draft Table', 'request_description': 'Customer name',
            'account_ids': [(6, 0, self.account.ids)],
        })
        unpublished = self.env['cs.ai.customer.share'].create({
            'name': 'Unpublished Table', 'request_description': 'Customer name',
            'account_ids': [(6, 0, self.account.ids)],
        })
        unpublished.write({'state': 'approved', 'portal_enabled': False})
        expired = self.env['cs.ai.customer.share'].create({
            'name': 'Expired Table', 'request_description': 'Customer name',
            'account_ids': [(6, 0, self.account.ids)],
            'expires_on': fields.Date.add(fields.Date.today(), days=-1),
        })
        expired.write({'state': 'approved', 'portal_enabled': True})
        with patch.object(type(self.share), '_refresh_approved_portal_table',
                          autospec=True, return_value=True) as refresh:
            self.env['cs.ai.customer.share']._cron_refresh_approved_portal_tables()
        refreshed_ids = {call.args[0].id for call in refresh.call_args_list}
        self.assertIn(self.share.id, refreshed_ids)
        self.assertNotIn(draft.id, refreshed_ids)
        self.assertNotIn(unpublished.id, refreshed_ids)
        self.assertNotIn(expired.id, refreshed_ids)
        self.assertTrue(all(
            call.kwargs.get('respect_portal_selection') is True
            for call in refresh.call_args_list))
