from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVocInsight(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env.ref('base.user_admin')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Voice Customer', 'is_company': True})
        cls.account = cls.env['cs.account'].with_user(cls.manager).create({
            'partner_id': cls.partner.id,
            'csm_user_id': cls.manager.id,
        })
        cls.account.with_context(cs_skip_recompute=True).write({
            'health_score': 80, 'health_status': 'good',
            'churn_probability': 5, 'sentiment_label': 'neutral',
            'usage_signal': 'active',
        })

    def _close_review(self):
        review = self.env['cs.value.review'].create({
            'cs_account_id': self.account.id,
            'review_date': fields.Date.today(),
            'next_review_date': fields.Date.today() + timedelta(days=90),
        })
        review.action_prepare()
        review.action_mark_held()
        review.write({
            'value_realized': 'Customer confirmed faster monthly processing.',
            'risks_and_blockers': 'A critical manual approval still blocks operations.',
            'customer_priorities': 'Remove the approval bottleneck.',
            'commitments': 'ERA will review the blocked process.',
            'evidence': 'Customer confirmed the blocker during the value review.',
        })
        review.action_close()
        return review

    def test_closed_review_creates_one_immutable_high_priority_insight(self):
        leads = self.env['crm.lead'].search_count([])
        account_messages = len(self.account.message_ids)
        review = self._close_review()
        insight = self.env['cs.voc.insight'].search([
            ('value_review_id', '=', review.id)])
        self.assertEqual(len(insight), 1)
        self.assertEqual(insight.priority, 'high')
        self.assertEqual(insight.sentiment, 'negative')
        self.assertIn('approval', insight.risks_or_blockers)
        self.env['cs.voc.insight']._capture_value_review(review)
        self.assertEqual(self.env['cs.voc.insight'].search_count([
            ('value_review_id', '=', review.id)]), 1)
        with self.assertRaises(UserError):
            insight.write({'summary': 'Changed snapshot'})
        with self.assertRaises(UserError):
            insight.write({'name': 'Changed source name'})
        with self.assertRaises(UserError):
            insight.with_user(self.manager).write({'acted_by_id': self.manager.id})
        with self.assertRaises(UserError):
            insight.with_user(self.manager).write({'state': 'closed'})
        self.assertEqual(self.env['crm.lead'].search_count([]), leads)
        self.assertEqual(len(self.account.message_ids), account_messages)

    def test_high_voice_enters_worklist_and_completion_marks_action(self):
        review = self._close_review()
        insight = self.env['cs.voc.insight'].search([
            ('value_review_id', '=', review.id)])
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertEqual(values['action_type'], 'voice_customer')
        self.assertEqual(values['voc_id'], insight.id)
        suggestion = self.env['cs.weekly.suggestion'].sudo().search([
            ('cs_account_id', '=', self.account.id),
            ('state', '=', 'open'),
        ], limit=1)
        self.assertEqual(suggestion.voc_id, insight)
        self.env['cs.suggestion.complete'].create({
            'suggestion_id': suggestion.id,
            'outcome': 'customer_contacted',
            'outcome_note': 'Reviewed the blocker and agreed an owner.',
            'schedule_activity': False,
        }).action_confirm()
        self.assertEqual(insight.state, 'acted')
        self.assertIn('agreed an owner', insight.action_note)

    def test_support_recovery_remains_higher_than_customer_voice(self):
        self._close_review()
        self.account.with_context(cs_skip_recompute=True).write({
            'sentiment_label': 'negative',
        })
        values = self.account._daily_work_item_values(fields.Date.today())
        self.assertEqual(values['action_type'], 'support_recovery')

    def test_adoption_confidence_controls_voice_priority(self):
        assessment = self.env['cs.adoption.assessment'].create({
            'cs_account_id': self.account.id,
            'assessment_date': fields.Date.today() - timedelta(days=1),
            'source_reference': 'Customer adoption review',
            'licensed_users': 20,
            'active_users_30d': 2,
            'key_workflows_total': 5,
            'adopted_workflows': 1,
            'usage_frequency': 'rare',
            'evidence': 'Customer shared aggregate adoption evidence.',
            'blockers': 'Key workflows remain unused.',
        })
        assessment.action_confirm()
        insight = self.env['cs.voc.insight'].search([
            ('adoption_assessment_id', '=', assessment.id)])
        self.assertEqual(insight.priority, 'high')
        self.assertEqual(insight.adoption_confidence, 75)

        low_confidence = self.env['cs.adoption.assessment'].create({
            'cs_account_id': self.account.id,
            'assessment_date': fields.Date.today(),
            'source_reference': 'Customer usage description',
            'usage_frequency': 'rare',
            'evidence': 'Customer described use as rare.',
        })
        low_confidence.action_confirm()
        insight = self.env['cs.voc.insight'].search([
            ('adoption_assessment_id', '=', low_confidence.id)])
        self.assertEqual(insight.priority, 'medium')
        self.assertEqual(insight.adoption_confidence, 25)
