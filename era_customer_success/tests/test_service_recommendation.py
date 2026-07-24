from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestServiceRecommendation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env.ref('base.user_admin')
        cls.partner = cls.env['res.partner'].create({'name': 'Recommendation Customer', 'is_company': True})
        cls.contact = cls.env['res.partner'].create({'name': 'Recommendation Contact', 'parent_id': cls.partner.id})
        cls.account = cls.env['cs.account'].with_user(cls.manager).create({
            'partner_id': cls.partner.id, 'csm_user_id': cls.manager.id})
        cls.account.with_context(cs_skip_recompute=True).write({
            'health_score': 80, 'health_status': 'good', 'churn_probability': 5,
            'sentiment_label': 'neutral', 'usage_signal': 'active'})
        cls.product = cls.env['product.template'].create({
            'name': 'ERA Adoption Service', 'type': 'service', 'list_price': 1000})
        cls.service = cls.env['cs.service'].create({
            'name': 'Adoption Enablement Service',
            'product_tmpl_ids': [(6, 0, cls.product.ids)],
            'recommend_on_low_adoption': True,
            'discovery_questions': 'Which workflows are blocked?',
            'not_suitable_when': 'No measurable adoption goal exists.',
        })

    def _assessment(self, complete=True):
        vals = {
            'cs_account_id': self.account.id, 'assessment_date': fields.Date.today(),
            'source_reference': 'Customer review', 'support_engagement': 'low',
            'evidence': 'Aggregate evidence shared by customer.'}
        if complete:
            vals.update({'project_engagement': 'blocked'})
        assessment = self.env['cs.adoption.assessment'].create(vals)
        assessment.action_confirm()

    def _wizard(self):
        wizard = self.env['cs.service.recommendation.wizard'].create({'cs_account_id': self.account.id})
        wizard.action_compute()
        return wizard

    def test_recommendation_draft_and_qualification_gate(self):
        self._assessment()
        wizard = self._wizard()
        self.assertEqual(wizard.line_ids.score, 40)
        leads = self.env['crm.lead'].search_count([])
        wizard.action_create_drafts()
        offering = self.env['csm.offering'].search([('cs_account_id', '=', self.account.id)])
        self.assertEqual(offering.state, 'draft')
        self.assertFalse(offering.opportunity_id)
        with self.assertRaises(UserError):
            offering.action_accept()
        with self.assertRaises(UserError):
            offering.with_user(self.manager).write({'state': 'presented'})
        with self.assertRaises(UserError):
            offering.action_present()
        offering.write({'customer_need': 'Improve workflow adoption.',
                        'customer_contact_id': self.contact.id,
                        'suitability_checked': True})
        offering.action_present()
        with self.assertRaises(UserError):
            offering.action_accept()
        offering.write({'customer_interest_confirmed': True, 'need_timing': 'quarter'})
        offering.action_accept()
        self.assertEqual(self.env['crm.lead'].search_count([]), leads + 1)
        with self.assertRaises(UserError):
            offering.action_accept()

    def test_low_confidence_and_confirmed_purchase_are_excluded(self):
        self._assessment(complete=False)
        wizard = self._wizard()
        self.assertFalse(wizard.line_ids)
        self.assertIn('No current customer signal matches', wizard.recommendation_status)

    def test_unconfigured_catalog_explains_why_no_suggestions_exist(self):
        self.service.write({
            'recommend_on_low_adoption': False,
            'recommend_on_support_pressure': False,
            'recommend_on_sla_failure': False,
        })

        wizard = self._wizard()

        self.assertFalse(wizard.line_ids)
        self.assertIn('No catalog service has recommendation conditions configured', wizard.recommendation_status)

    def test_explicit_success_plan_link_and_idempotency(self):
        self.env['cs.success.profile'].create({
            'cs_account_id': self.account.id, 'state': 'active',
            'recommended_service_ids': [(6, 0, self.service.ids)]})
        wizard = self._wizard()
        self.assertEqual(wizard.line_ids.score, 35)
        wizard.action_create_drafts()
        self._wizard().action_create_drafts()
        self.assertEqual(self.env['csm.offering'].search_count([
            ('cs_account_id', '=', self.account.id), ('service_id', '=', self.service.id)]), 1)

    def test_recent_rejection_uses_rejection_date_for_cooldown(self):
        self._assessment()
        wizard = self._wizard()
        wizard.action_create_drafts()
        offering = self.env['csm.offering'].search([
            ('cs_account_id', '=', self.account.id),
            ('service_id', '=', self.service.id),
        ])
        offering.sudo().write({
            'offering_date': fields.Date.today() - timedelta(days=120)})
        offering.action_reject()
        self.assertFalse(self._wizard().line_ids)

    def test_legacy_presented_offering_cannot_bypass_qualification(self):
        offering = self.env['csm.offering'].create({
            'name': 'Legacy presented offering',
            'cs_account_id': self.account.id,
            'partner_id': self.partner.id,
            'service_id': self.service.id,
        })
        offering.sudo().write({
            'state': 'presented',
            'customer_interest_confirmed': True,
            'need_timing': 'now',
        })
        with self.assertRaises(UserError):
            offering.action_accept()
