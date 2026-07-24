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
