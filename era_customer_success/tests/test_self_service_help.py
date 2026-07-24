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
                self.assertIn('Best use:', arch)
                self.assertIn('Features:', arch)
                self.assertIn('Priority:', arch)
