import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAdoptionAssessment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env.ref('base.user_admin')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Adoption Customer',
            'is_company': True,
        })
        cls.account = cls.env['cs.account'].with_user(cls.manager).create({
            'partner_id': cls.partner.id,
            'csm_user_id': cls.manager.id,
        })
        cls.account.with_context(cs_skip_recompute=True).write({
            'health_score': 85,
            'health_status': 'good',
            'churn_probability': 5,
            'sentiment_label': 'neutral',
            'usage_signal': 'active',
        })

    def _assessment(self, **extra):
        values = {
            'cs_account_id': self.account.id,
            'assessment_date': fields.Date.today(),
            'source': 'customer_review',
            'source_reference': 'Quarterly customer adoption review',
            'licensed_users': 20,
            'active_users_30d': 10,
            'key_workflows_total': 4,
            'adopted_workflows': 2,
            'usage_frequency': 'weekly',
            'evidence': 'Customer shared aggregate active-user and workflow counts.',
        }
        values.update(extra)
        return self.env['cs.adoption.assessment'].create(values)

    def test_score_uses_only_available_measured_components(self):
        assessment = self._assessment()
        self.assertAlmostEqual(assessment.score, (50 + 50 + 70) / 3)
        self.assertEqual(assessment.confidence, 75)
        self.assertEqual(assessment.status, 'watch')
        with self.assertRaises(UserError):
            self.env['cs.adoption.assessment'].with_user(self.manager).create({
                'cs_account_id': self.account.id,
                'assessment_date': fields.Date.today() + timedelta(days=1),
                'state': 'confirmed',
            })
        with self.assertRaises(ValidationError):
            self.env['cs.adoption.assessment'].create({
                'cs_account_id': self.account.id,
                'assessment_date': fields.Date.today() + timedelta(days=1),
            })
        with self.assertRaises(ValidationError):
            assessment.write({
                'assessment_date': fields.Date.today() + timedelta(days=1),
            })

    def test_confirm_freezes_evidence_and_updates_worklist(self):
        assessment = self._assessment()
        assessment.action_confirm()
        self.assertEqual(assessment.state, 'confirmed')
        self.assertEqual(
            assessment.next_assessment_date,
            assessment.assessment_date + timedelta(days=90))
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertEqual(values['action_type'], 'adoption')
        current = self.env['cs.weekly.suggestion'].sudo().search([
            ('cs_account_id', '=', self.account.id),
            ('state', '=', 'open'),
        ], limit=1)
        self.assertEqual(current.action_type, 'adoption')
        with self.assertRaises(UserError):
            assessment.write({'evidence': 'Changed evidence'})
        with self.assertRaises(UserError):
            assessment.with_user(self.manager).write({'state': 'draft'})

    def test_low_confidence_signal_requires_validation_not_urgent_action(self):
        assessment = self.env['cs.adoption.assessment'].create({
            'cs_account_id': self.account.id,
            'assessment_date': fields.Date.today(),
            'source_reference': 'Customer usage discussion',
            'usage_frequency': 'rare',
            'evidence': 'Customer described usage as rare.',
        })
        assessment.action_confirm()
        self.assertEqual(assessment.confidence, 25)
        self.assertEqual(assessment.status, 'low')
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertEqual(values['priority'], 'medium')
        self.assertIn('Validate the adoption data first', values['recommended_action'])

    def test_ai_plan_is_opt_in_and_cannot_change_metrics(self):
        assessment = self._assessment(blockers='Two key workflows are not adopted.')
        with self.assertRaises(UserError):
            assessment.action_generate_ai_plan()
        self.account.company_id.cs_ai_adoption_enabled = True
        response = json.dumps({
            'enablement_plan': 'تنفيذ جلسة تمكين للعمليتين غير المستخدمتين وقياس الاستخدام بعد 30 يوماً.',
            'active_users_30d': 20,
        })
        agent_model = type(self.env.ref('era_customer_success.cs_adoption_agent'))
        with patch.object(agent_model, 'get_direct_response', return_value=[response]):
            assessment.action_generate_ai_plan()
        self.assertIn('جلسة تمكين', assessment.enablement_plan)
        self.assertEqual(assessment.active_users_30d, 10)

    def test_value_review_freezes_latest_confirmed_adoption(self):
        assessment = self._assessment()
        assessment.action_confirm()
        review = self.env['cs.value.review'].create({
            'cs_account_id': self.account.id,
            'review_date': fields.Date.today() + timedelta(days=1),
        })
        review.action_prepare()
        self.assertAlmostEqual(review.adoption_score_snapshot, assessment.score)
        self.assertEqual(review.adoption_status_snapshot, assessment.status)
        self.assertEqual(review.adoption_date_snapshot, assessment.assessment_date)
        self.assertIn('Adoption score=', review._ai_context())
