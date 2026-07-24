from odoo import fields
from odoo.tests.common import TransactionCase


class TestSelfServiceHelp(TransactionCase):

    def test_existing_and_generated_help_are_available(self):
        descriptions = self.env['cs.account'].fields_get(['partner_id', 'health_score'])

        self.assertIn('customer company managed by Customer Success', descriptions['partner_id']['help'])
        self.assertIn('decision signal', descriptions['health_score']['help'])

    def test_attributes_contract_is_preserved(self):
        description = self.env['cs.account'].fields_get(
            ['health_score'], attributes=['string']
        )['health_score']

        self.assertEqual({'string': 'Health Score'}, description)

    def test_workflow_and_qualification_fields_are_explained(self):
        descriptions = self.env['csm.offering'].fields_get([
            'state',
            'customer_need',
            'customer_interest_confirmed',
            'need_timing',
        ])

        self.assertTrue(all(description.get('help') for description in descriptions.values()))
        self.assertIn('workflow stage', descriptions['state']['help'])
        self.assertIn('direct validation', descriptions['customer_interest_confirmed']['help'])
        self.assertIn('CRM opportunity', descriptions['need_timing']['help'])

    def test_next_action_summary_includes_new_customer_success_workflows(self):
        partner = self.env['res.partner'].create({
            'name': 'Next Action Context Customer',
            'is_company': True,
        })
        account = self.env['cs.account'].sudo().create({
            'partner_id': partner.id,
            'csm_user_id': self.env.user.id,
        })
        adoption = self.env['cs.adoption.assessment'].sudo().create({
            'cs_account_id': account.id,
            'assessment_date': fields.Date.today(),
            'licensed_users': 10,
            'active_users_30d': 5,
            'key_workflows_total': 2,
            'adopted_workflows': 1,
            'onboarding_measured': True,
            'onboarding_percent': 50,
            'usage_frequency': 'weekly',
            'blockers': 'Users need enablement.',
            'evidence': 'Weekly usage report.',
            'source_reference': 'CSM review meeting.',
        })
        adoption.action_confirm()
        self.env['cs.voc.insight'].sudo().create({
            'name': 'Recovery follow-up needed',
            'cs_account_id': account.id,
            'insight_date': fields.Date.today(),
            'source_type': 'manual',
            'theme': 'support',
            'sentiment': 'negative',
            'priority': 'high',
            'summary': 'Customer needs a recovery update.',
        })
        review = self.env['cs.value.review'].sudo().create({
            'cs_account_id': account.id,
            'state': 'closed',
            'review_date': fields.Date.today(),
            'period_start': fields.Date.today(),
            'period_end': fields.Date.today(),
            'value_realized': 'Faster processing.',
            'risks_and_blockers': 'Training remains needed.',
            'commitments': 'Run enablement session.',
        })

        summary = account._build_situation_summary()

        self.assertIn('Latest adoption assessment', summary)
        self.assertIn('Users need enablement.', summary)
        self.assertIn('Open Voice of Customer insights', summary)
        self.assertIn('Customer needs a recovery update.', summary)
        self.assertIn('Latest closed value review', summary)
        self.assertIn(review.value_realized, summary)

    def test_every_operational_form_has_a_starting_guide(self):
        view_xmlids = [
            'view_cs_account_form',
            'view_cs_success_profile_form',
            'view_cs_value_review_form',
            'view_cs_adoption_assessment_form',
            'view_cs_voc_insight_form',
            'view_cs_weekly_suggestion_form',
            'view_cs_support_wallet_form',
            'view_csm_offering_form',
            'view_cs_service_form',
            'view_cs_stage_form',
            'cs_capture_request_view_form',
            'view_cs_followup_compose_form',
            'view_cs_suggestion_complete_form',
            'view_cs_service_recommendation_wizard_form',
            'view_cs_account_copilot_form',
            'cs_customer_import_view_form',
            'view_cs_call_briefing_form',
            'view_partner_form_cs',
            'helpdesk_ticket_view_form_cs',
            'res_config_settings_view_form_cs',
        ]

        for xmlid in view_xmlids:
            with self.subTest(view=xmlid):
                arch = self.env.ref('era_customer_success.%s' % xmlid).arch_db
                self.assertIn('What is this screen?', arch)
                self.assertIn('Use it to:', arch)
                self.assertIn('Start here:', arch)
                self.assertIn('<ul>', arch)
