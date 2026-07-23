from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDailyWorklist(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.csm = cls.env['res.users'].create({
            'name': 'CS Worklist User',
            'login': 'cs-worklist-test',
            'group_ids': [(4, cls.env.ref(
                'era_customer_success.group_era_cs_user').id)],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'CS Worklist Customer',
            'is_company': True,
        })
        cls.account = cls.env['cs.account'].with_user(cls.env.ref('base.user_admin')).create({
            'partner_id': cls.partner.id,
            'csm_user_id': cls.csm.id,
            'cadence': 'weekly',
        })

    def test_daily_worklist_prioritizes_critical_risk(self):
        self.account.with_context(cs_skip_recompute=True).write({
            'health_status': 'critical',
            'health_score': 25,
        })
        self.env['cs.weekly.suggestion']._cron_build_daily_worklist()
        item = self.env['cs.weekly.suggestion'].sudo().search([
            ('cs_account_id', '=', self.account.id),
            ('state', '=', 'open'),
        ], limit=1)
        self.assertTrue(item)
        self.assertEqual(item.priority, 'urgent')
        self.assertEqual(item.action_type, 'risk_recovery')

    def test_completed_history_is_not_overwritten(self):
        worklist = self.env['cs.weekly.suggestion']
        week = worklist._week_start()
        item = worklist.sudo().create({
            'cs_account_id': self.account.id,
            'week': week,
            'due_date': fields.Date.today(),
            'state': 'done',
            'outcome': 'customer_contacted',
            'outcome_note': 'Customer confirmed the next review.',
        })
        result = worklist._upsert_automated_item(self.account, {
            'source': 'ai',
            'priority': 'urgent',
            'reason': 'New AI reason',
            'recommended_action': 'New AI action',
            'due_date': fields.Date.today(),
        }, week=week)
        self.assertNotEqual(result, item)
        self.assertEqual(item.state, 'done')
        self.assertNotEqual(item.reason, 'New AI reason')
        self.assertEqual(result.state, 'open')
        self.assertEqual(result.reason, 'New AI reason')

    def test_daily_worklist_archives_stale_open_items(self):
        worklist = self.env['cs.weekly.suggestion']
        stale = worklist.sudo().create({
            'cs_account_id': self.account.id,
            'week': worklist._week_start() - timedelta(days=7),
            'due_date': fields.Date.today() - timedelta(days=7),
            'reason': 'Old weekly priority.',
            'recommended_action': 'Old action.',
        })
        worklist._cron_build_daily_worklist()
        self.assertEqual(stale.state, 'dismissed')
        self.assertEqual(stale.outcome, 'not_relevant')
        self.assertTrue(stale.completed_on)

    def test_completion_schedules_next_step(self):
        item = self.env['cs.weekly.suggestion'].sudo().create({
            'cs_account_id': self.account.id,
            'week': self.env['cs.weekly.suggestion']._week_start(),
            'due_date': fields.Date.today(),
            'reason': 'Relationship follow-up is due.',
            'recommended_action': 'Contact the customer.',
        })
        next_date = fields.Date.today() + timedelta(days=3)
        wizard = self.env['cs.suggestion.complete'].with_user(self.csm).create({
            'suggestion_id': item.id,
            'outcome': 'followup_required',
            'outcome_note': 'Customer requested a follow-up after the internal review.',
            'next_step': 'Review the customer decision',
            'next_step_date': next_date,
            'schedule_activity': True,
        })
        wizard.action_confirm()
        self.assertEqual(item.state, 'done')
        self.assertEqual(item.completed_by_id, self.csm)
        activity = self.account.activity_ids.filtered(
            lambda act: act.summary == 'Review the customer decision')
        self.assertTrue(activity)
        self.assertEqual(activity.date_deadline, next_date)
