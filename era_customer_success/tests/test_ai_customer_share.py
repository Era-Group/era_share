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
