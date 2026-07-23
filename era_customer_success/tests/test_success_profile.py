import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSuccessProfile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env.ref('base.user_admin')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Success Plan Customer',
            'is_company': True,
        })
        cls.contact = cls.env['res.partner'].create({
            'name': 'Customer Champion',
            'parent_id': cls.partner.id,
            'function': 'Operations Manager',
        })
        cls.account = cls.env['cs.account'].with_user(cls.manager).create({
            'partner_id': cls.partner.id,
            'csm_user_id': cls.manager.id,
            'cadence': 'monthly',
        })
        cls.account.with_context(cs_skip_recompute=True).write({
            'health_status': 'good',
            'health_score': 85,
            'churn_probability': 5,
            'sentiment_label': 'neutral',
            'usage_signal': 'active',
        })

    def test_account_opens_single_success_plan(self):
        first = self.account.action_open_success_profile()
        second = self.account.action_open_success_profile()
        self.assertEqual(first['res_id'], second['res_id'])
        self.assertEqual(len(self.account.success_profile_ids), 1)

    def test_stakeholder_must_belong_to_customer(self):
        profile = self.env['cs.success.profile'].create({
            'cs_account_id': self.account.id,
        })
        outsider = self.env['res.partner'].create({'name': 'Outside Contact'})
        with self.assertRaises(ValidationError):
            self.env['cs.success.stakeholder'].create({
                'profile_id': profile.id,
                'partner_id': outsider.id,
            })

    def test_overdue_milestone_enters_daily_worklist(self):
        profile = self.env['cs.success.profile'].create({
            'cs_account_id': self.account.id,
            'state': 'active',
        })
        self.env['cs.success.milestone'].create({
            'profile_id': profile.id,
            'name': 'Complete adoption review',
            'target_date': fields.Date.today() - timedelta(days=1),
            'priority': 'high',
        })
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertEqual(values['action_type'], 'success_milestone')
        self.assertEqual(values['priority'], 'high')

    def test_draft_milestones_wait_for_review_and_priority_is_numeric(self):
        profile = self.env['cs.success.profile'].create({
            'cs_account_id': self.account.id,
        })
        target = fields.Date.today()
        self.env['cs.success.milestone'].create({
            'profile_id': profile.id,
            'name': 'Low priority milestone',
            'target_date': target,
            'priority': 'low',
        })
        self.env['cs.success.milestone'].create({
            'profile_id': profile.id,
            'name': 'High priority milestone',
            'target_date': target,
            'priority': 'high',
        })
        draft_values = self.account._daily_work_item_values(target)
        self.assertNotEqual(draft_values['action_type'], 'success_milestone')
        profile.action_activate()
        active_values = self.account._daily_work_item_values(target)
        self.assertEqual(active_values['action_type'], 'success_milestone')
        self.assertIn('High priority milestone', active_values['reason'])
        current = self.env['cs.weekly.suggestion'].sudo().search([
            ('cs_account_id', '=', self.account.id),
            ('state', '=', 'open'),
            ('action_type', '=', 'success_milestone'),
        ], limit=1)
        self.assertTrue(current)
        profile.action_set_draft()
        self.assertFalse(self.env['cs.weekly.suggestion'].sudo().search([
            ('id', '=', current.id),
            ('state', '=', 'open'),
            ('action_type', '=', 'success_milestone'),
        ]))

    def test_new_milestone_opens_after_completed_weekly_item(self):
        worklist = self.env['cs.weekly.suggestion'].sudo()
        worklist.create({
            'cs_account_id': self.account.id,
            'week': worklist._week_start(),
            'due_date': fields.Date.today(),
            'state': 'done',
            'outcome': 'customer_contacted',
            'outcome_note': 'Earlier work completed.',
        })
        profile = self.env['cs.success.profile'].create({
            'cs_account_id': self.account.id,
        })
        self.env['cs.success.milestone'].create({
            'profile_id': profile.id,
            'name': 'New urgent customer milestone',
            'target_date': fields.Date.today(),
            'priority': 'urgent',
        })
        profile.write({'state': 'active'})
        current = worklist.search([
            ('cs_account_id', '=', self.account.id),
            ('state', '=', 'open'),
            ('action_type', '=', 'success_milestone'),
        ])
        self.assertEqual(len(current), 1)

    def test_ai_draft_requires_opt_in_and_deduplicates(self):
        profile = self.env['cs.success.profile'].create({
            'cs_account_id': self.account.id,
        })
        with self.assertRaises(UserError):
            profile.action_generate_ai_draft()

        self.account.company_id.cs_ai_success_plan_enabled = True
        response = json.dumps({
            'business_objectives': 'رفع تبني النظام.',
            'challenges': 'ضعف استخدام بعض الخصائص.',
            'desired_outcomes': 'استخدام مستقر من الفريق.',
            'success_criteria': 'إتمام مراجعة التبني.',
            'value_hypothesis': 'جلسة تمكين مركزة.',
            'stakeholders': [{
                'name': self.contact.name,
                'role': 'champion',
                'influence': 'high',
            }],
            'milestones': [{
                'name': 'مراجعة التبني',
                'description': 'مراجعة الاستخدام مع العميل.',
                'success_criterion': 'توثيق خطة تحسين.',
                'target_in_days': 14,
                'priority': 'high',
            }],
        })
        agent_model = type(self.env.ref('era_customer_success.cs_success_plan_agent'))
        with patch.object(agent_model, 'get_direct_response', return_value=[response]):
            profile.action_generate_ai_draft()
            profile.action_generate_ai_draft()
        self.assertEqual(profile.business_objectives, 'رفع تبني النظام.')
        self.assertEqual(len(profile.stakeholder_ids), 1)
        self.assertEqual(profile.stakeholder_ids.partner_id, self.contact)
        self.assertEqual(len(profile.milestone_ids), 1)
        profile.action_activate()
        with self.assertRaises(UserError):
            profile.action_generate_ai_draft()
