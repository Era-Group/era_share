from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGoogleSheetScope(TransactionCase):

    def test_sync_updates_approved_red_columns_only(self):
        sync = self.env['cs.google.sheet.sync']
        rows = [['ERA CSM', 'ERA CSM phone number', 'ERA CSM email', 'Customer Name',
                 'Date of Join', 'Next invoice date', 'Recurring plan', 'Industry',
                 '# of Employees', '# of Users', 'Active Users', 'Stage', 'Version',
                 'Adoption', 'Client Website', 'Active Implemented modules',
                 'Potential Expansion', 'Next Action', 'Extra Notes', 'Expansion Status']]
        with patch.object(type(sync), '_settings', return_value={
                'enabled': True, 'spreadsheet_id': 'sheet', 'gid': 1,
                'credentials': '{}', 'sharing_approved': True,
                'approval_scope': 'A,B,C,H,I,L,O,P'}), \
                patch.object(type(sync), '_access_token', return_value='token'), \
                patch.object(type(sync), '_sheet_title', return_value='Portfolio'), \
                patch.object(type(sync), '_red_header_columns',
                             return_value={'A', 'B', 'C', 'H', 'I', 'L', 'O', 'P'}), \
                patch.object(type(sync), '_request', side_effect=[{'values': rows}, {}]) as request_mock:
            sync.action_sync()
        payload = request_mock.call_args_list[-1].kwargs.get('json', {}).get('data', [])
        allowed = {'A', 'B', 'C', 'H', 'I', 'L', 'O', 'P'}
        self.assertTrue(all(item['range'].split('!')[1][0] in allowed for item in payload))

    def test_dropdown_value_uses_exact_sheet_option(self):
        sync = self.env['cs.google.sheet.sync']
        value = sync._validated_dropdown_value(
            'live', [' Live', 'Cancelled', 'On Hold'], 'L', 'Stage')
        self.assertEqual(value, ' Live')

    def test_dropdown_rejects_value_outside_sheet_options(self):
        sync = self.env['cs.google.sheet.sync']
        with self.assertRaisesRegex(UserError, 'Allowed values'):
            sync._validated_dropdown_value(
                'Implementation', ['Live', 'Cancelled', 'On Hold'], 'L', 'Stage')

    def test_one_of_range_loads_allowed_values(self):
        sync = self.env['cs.google.sheet.sync']
        condition = {
            'type': 'ONE_OF_RANGE',
            'values': [{'userEnteredValue': "='Lists'!$A$1:$A$3"}],
        }
        with patch.object(type(sync), '_request', return_value={
                'values': [['Done'], ['Pending'], ['No potential']]}) as request_mock:
            options = sync._validation_condition_options(
                condition, 'sheet', 'Portfolio', 'token')
        self.assertEqual(options, ['Done', 'Pending', 'No potential'])
        self.assertIn('Lists', request_mock.call_args.args[1])
