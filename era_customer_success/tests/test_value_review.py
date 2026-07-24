import json
from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestValueReview(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env.ref('base.user_admin')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Value Review Customer',
            'is_company': True,
        })
        cls.account = cls.env['cs.account'].with_user(cls.manager).create({
            'partner_id': cls.partner.id,
            'csm_user_id': cls.manager.id,
        })
        cls.account.with_context(cs_skip_recompute=True).write({
            'health_score': 82,
            'health_status': 'good',
            'churn_probability': 5,
            'sentiment_label': 'neutral',
            'usage_signal': 'active',
        })
        cls.profile = cls.env['cs.success.profile'].create({
            'cs_account_id': cls.account.id,
            'state': 'active',
            'business_objectives': 'Reduce manual operational work.',
            'success_criteria': 'Complete the agreed process improvements.',
            'review_date': fields.Date.today() + timedelta(days=10),
        })
        cls.milestone = cls.env['cs.success.milestone'].create({
            'profile_id': cls.profile.id,
            'name': 'Complete adoption review',
            'target_date': fields.Date.today() + timedelta(days=5),
            'priority': 'high',
        })

    def _new_review(self):
        return self.env['cs.value.review'].create({
            'cs_account_id': self.account.id,
            'review_date': fields.Date.today() + timedelta(days=10),
            'next_review_date': fields.Date.today() + timedelta(days=100),
        })

    def test_prepare_freezes_customer_value_data(self):
        review = self._new_review()
        review.action_prepare()
        self.assertEqual(review.state, 'prepared')
        self.assertEqual(review.health_score_snapshot, 82)
        self.assertEqual(review.objectives_snapshot, self.profile.business_objectives)
        self.assertIn(self.milestone.name, review.milestones_snapshot)
        self.account.with_context(cs_skip_recompute=True).health_score = 20
        self.assertEqual(review.health_score_snapshot, 82)
        with self.assertRaises(UserError):
            review.write({'health_score_snapshot': 10})
        with self.assertRaises(UserError):
            review.with_user(self.manager).write({'state': 'closed'})
        with self.assertRaises(UserError):
            review.with_user(self.manager).with_context(
                cs_value_review_workflow=True).write({'state': 'closed'})

    def test_default_period_is_the_previous_three_calendar_months(self):
        review = self.env['cs.value.review'].create({
            'cs_account_id': self.account.id,
            'review_date': date(2026, 7, 24),
        })

        self.assertEqual(review.period_start, date(2026, 4, 1))
        self.assertEqual(review.period_end, date(2026, 7, 24))

    def test_csm_can_define_scope_before_prepare_but_not_after(self):
        review = self.env['cs.value.review'].with_user(self.manager).create({
            'cs_account_id': self.account.id,
            'review_date': date(2026, 7, 24),
            'period_start': date(2026, 4, 1),
            'period_end': date(2026, 6, 30),
        })
        review.with_user(self.manager).write({
            'period_start': date(2026, 4, 2),
        })
        self.assertEqual(review.period_start, date(2026, 4, 2))

        review.with_user(self.manager).action_prepare()

        with self.assertRaises(UserError):
            review.with_user(self.manager).write({
                'period_start': date(2026, 4, 1),
            })

    def test_csm_cannot_inject_snapshot_values(self):
        with self.assertRaises(UserError):
            self.env['cs.value.review'].with_user(self.manager).create({
                'cs_account_id': self.account.id,
                'review_date': date(2026, 7, 24),
                'health_score_snapshot': 100,
            })

    def test_prepare_requires_a_draft_review(self):
        review = self._new_review()
        review.action_prepare()

        with self.assertRaises(UserError):
            review.action_prepare()

    def test_close_updates_plan_and_schedules_one_followup_without_crm(self):
        review = self._new_review()
        review.action_prepare()
        review.action_mark_held()
        next_step_date = fields.Date.today() + timedelta(days=7)
        review.write({
            'value_realized': 'Customer confirmed faster monthly processing.',
            'commitments': 'ERA will review adoption with the operations team.',
            'next_step': 'Run adoption review',
            'next_step_date': next_step_date,
        })
        lead_count = self.env['crm.lead'].search_count([
            ('partner_id', '=', self.partner.id),
        ])
        review.action_close()
        self.assertEqual(review.state, 'closed')
        self.assertEqual(self.profile.review_date, review.next_review_date)
        self.assertEqual(self.profile.last_reviewed_on, review.review_date)
        activities = self.account.activity_ids.filtered(
            lambda activity: activity.summary == 'Run adoption review')
        self.assertEqual(len(activities), 1)
        self.assertEqual(self.env['crm.lead'].search_count([
            ('partner_id', '=', self.partner.id),
        ]), lead_count)
        current = self.env['cs.weekly.suggestion'].sudo().search([
            ('cs_account_id', '=', self.account.id),
            ('state', '=', 'open'),
            ('action_type', '=', 'value_review'),
        ])
        self.assertFalse(current)

    def test_ai_is_opt_in_and_does_not_confirm_value(self):
        review = self._new_review()
        review.action_prepare()
        with self.assertRaises(UserError):
            review.action_generate_ai_draft()
        self.account.company_id.cs_ai_value_review_enabled = True
        response = json.dumps({
            'agenda': 'مراجعة الأهداف والقيمة.',
            'data_observations': 'درجة الصحة مستقرة.',
            'discussion_questions': 'ما الأولوية القادمة؟',
            'risks_and_blockers': 'يلزم التحقق من التبني.',
            'potential_needs': 'حاجة محتملة إلى جلسة تمكين.',
            'value_realized': 'Must not be applied',
            'commitments': 'Must not be applied',
        })
        agent_model = type(self.env.ref('era_customer_success.cs_value_review_agent'))
        with patch.object(agent_model, 'get_direct_response', return_value=[response]):
            review.action_generate_ai_draft()
        self.assertEqual(review.data_observations, 'درجة الصحة مستقرة.')
        self.assertFalse(review.value_realized)
        self.assertFalse(review.commitments)

    def test_cron_is_idempotent_and_review_enters_worklist(self):
        self.milestone.write({'state': 'achieved'})
        self.env['cs.value.review']._cron_prepare_upcoming_reviews()
        self.env['cs.value.review']._cron_prepare_upcoming_reviews()
        reviews = self.env['cs.value.review'].search([
            ('cs_account_id', '=', self.account.id),
            ('review_date', '=', self.profile.review_date),
        ])
        self.assertEqual(len(reviews), 1)
        current = self.env['cs.weekly.suggestion'].sudo().search([
            ('cs_account_id', '=', self.account.id),
            ('state', '=', 'open'),
        ], limit=1)
        self.assertEqual(current.action_type, 'value_review')
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertEqual(values['action_type'], 'value_review')
        reviews.action_cancel()
        self.env['cs.value.review']._cron_prepare_upcoming_reviews()
        self.assertEqual(self.env['cs.value.review'].search_count([
            ('cs_account_id', '=', self.account.id),
            ('review_date', '=', self.profile.review_date),
        ]), 1)
