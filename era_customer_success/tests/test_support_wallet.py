from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSupportWallet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env.ref('base.user_admin')
        cls.company = cls.manager.company_id
        cls.company.write({
            'cs_support_validity_days': 365,
            'cs_support_low_threshold': 25.0,
            'cs_support_critical_threshold': 10.0,
            'cs_support_expiry_warning_days': 30,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Support Wallet Customer',
            'is_company': True,
        })
        cls.account = cls.env['cs.account'].with_user(cls.manager).create({
            'partner_id': cls.partner.id,
            'csm_user_id': cls.manager.id,
        })
        cls.account.with_context(cs_skip_recompute=True).write({
            'health_status': 'good',
            'health_score': 90,
            'churn_probability': 5,
            'sentiment_label': 'neutral',
            'usage_signal': 'active',
        })
        cls.product = cls.env['product.product'].create({
            'name': '10 Support Hours',
            'type': 'service',
            'uom_id': cls.env.ref('uom.product_uom_hour').id,
            'service_policy': 'ordered_prepaid',
            'service_tracking': 'no',
            'list_price': 1000.0,
        })
        cls.company.cs_support_product_tmpl_ids = [(4, cls.product.product_tmpl_id.id)]
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'date_order': datetime.now() - timedelta(days=400),
            'order_line': [(0, 0, {
                'product_id': cls.product.id,
                'product_uom_qty': 10.0,
                'price_unit': 100.0,
            })],
        })
        cls.order.action_confirm()
        cls.sale_line = cls.order.order_line
        cls.order.write({'date_order': datetime.now() - timedelta(days=400)})
        cls.env.flush_all()
        cls.env.invalidate_all()

    def _wallet(self):
        return self.env['cs.support.wallet'].search([
            ('sale_line_id', '=', self.sale_line.id),
        ])

    def test_configured_prepaid_hours_are_discovered(self):
        wallet = self._wallet()
        self.assertEqual(len(wallet), 1)
        self.assertEqual(wallet.cs_account_id, self.account)
        self.assertEqual(wallet.purchased_hours, 10.0)
        self.assertEqual(wallet.remaining_hours, 10.0)
        self.assertEqual(wallet.status, 'expired')

    def test_low_balance_enters_daily_worklist(self):
        self.env.cr.execute(
            'UPDATE sale_order_line SET remaining_hours = %s WHERE id = %s',
            (0.5, self.sale_line.id))
        self.env.cr.execute(
            'UPDATE sale_order SET date_order = %s WHERE id = %s',
            (fields.Datetime.now(), self.order.id))
        self.env.invalidate_all()
        wallet = self._wallet()
        self.assertEqual(wallet.status, 'critical')
        self.assertEqual(wallet.used_hours, 9.5)
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertEqual(values['action_type'], 'support_hours')
        self.assertEqual(values['priority'], 'medium')

    def test_wallet_starts_need_discovery_not_opportunity(self):
        wallet = self._wallet()
        action = wallet.action_explore_support_need()
        context = action['context']
        self.assertEqual(action['res_model'], 'csm.offering')
        self.assertEqual(context['default_need_type'], 'support_hours')
        self.assertEqual(context['default_support_sale_line_id'], self.sale_line.id)
        self.assertFalse(self.env['crm.lead'].search([
            ('partner_id', '=', self.partner.id),
            ('cs_is_upsell', '=', True),
        ]))

    def test_new_healthy_package_suppresses_old_expiry_alert(self):
        new_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 20.0,
                'price_unit': 100.0,
            })],
        })
        new_order.action_confirm()
        self.env.flush_all()
        self.env.invalidate_all()
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertNotEqual(values['action_type'], 'support_hours')
        self.assertEqual(self.account.support_hours_remaining, 20.0)
        self.assertEqual(self.account.support_wallet_status, 'healthy')

    def test_offering_rejects_another_customers_package(self):
        other_partner = self.env['res.partner'].create({
            'name': 'Other Support Customer',
            'is_company': True,
        })
        other_order = self.env['sale.order'].create({
            'partner_id': other_partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 5.0,
                'price_unit': 100.0,
            })],
        })
        other_order.action_confirm()
        with self.assertRaises(ValidationError):
            self.env['csm.offering'].create({
                'name': 'Invalid support need',
                'cs_account_id': self.account.id,
                'partner_id': self.partner.id,
                'company_id': self.company.id,
                'need_type': 'support_hours',
                'support_sale_line_id': other_order.order_line.id,
            })
        with self.assertRaises(ValidationError):
            self.env['csm.offering'].create({
                'name': 'Mismatched offering customer',
                'cs_account_id': self.account.id,
                'partner_id': other_partner.id,
                'company_id': self.company.id,
                'need_type': 'support_hours',
                'support_sale_line_id': self.sale_line.id,
            })
