from unittest.mock import patch

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
