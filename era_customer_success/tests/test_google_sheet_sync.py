from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.era_customer_success.models.google_sheet_sync import (
    _customer_name_score,
    _normalize_customer_name,
)


@tagged('post_install', '-at_install')
class TestGoogleSheetScope(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({
            'name': 'Matched Customer', 'is_company': True,
        })
        cls.account = cls.env['cs.account'].create({
            'partner_id': partner.id,
            'company_id': cls.env.company.id,
            'csm_user_id': False,
        })

    def test_customer_name_normalization_handles_arabic_variants_and_company_words(self):
        self.assertEqual(_normalize_customer_name('شركة الرائحة الفواحة المحدودة'),
                         _normalize_customer_name('الرائحه الفواحه'))

    def test_customer_name_score_accepts_partial_company_name(self):
        score, reason = _customer_name_score('Tad Group Int.', 'Tad')
        self.assertGreaterEqual(score, 80)
        self.assertIn('name', reason)

    def test_customer_name_score_does_not_match_unrelated_names(self):
        score, _reason = _customer_name_score('First Tire', 'Flint Manufacturing')
        self.assertEqual(score, 0)

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

    def test_module_dropdown_accepts_and_normalizes_multiple_options(self):
        sync = self.env['cs.google.sheet.sync']
        value = sync._validated_dropdown_value(
            ['sales', 'Accounting', 'Sales'],
            ['Sales', 'Accounting', 'Inventory'],
            'P', 'Active Implemented modules', multiple=True)
        self.assertEqual(value, 'Sales, Accounting')

    def test_module_dropdown_rejects_any_invalid_option(self):
        sync = self.env['cs.google.sheet.sync']
        with self.assertRaisesRegex(UserError, 'Allowed values'):
            sync._validated_dropdown_value(
                'Sales, Unknown Module', ['Sales', 'Accounting'],
                'P', 'Active Implemented modules', multiple=True)

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

    def test_full_excel_import_clears_local_values_missing_from_row(self):
        sync = self.env['cs.google.sheet.sync']
        row = [''] * 20
        row[3] = 'Customer from Excel'
        values = sync._account_values_from_sheet_row(
            row, set('ABCDEFGHIJKLMNOPQRST'), clear_missing=True)
        self.assertEqual(values['sheet_customer_name'], 'Customer from Excel')
        self.assertFalse(values['sheet_next_action'])
        self.assertFalse(values['sheet_extra_notes'])

    def test_partial_import_preserves_missing_local_values(self):
        sync = self.env['cs.google.sheet.sync']
        row = [''] * 20
        values = sync._account_values_from_sheet_row(row, {'R', 'S'})
        self.assertNotIn('sheet_next_action', values)
        self.assertNotIn('sheet_extra_notes', values)

    def test_ai_dropdown_options_include_only_approved_red_columns(self):
        sync = self.env['cs.google.sheet.sync']
        validations = {
            (4, 'L'): ['Live', 'Cancelled'],
            (4, 'P'): ['Sales', 'Accounting'],
            (4, 'T'): ['Done', 'Pending'],
        }
        with patch.object(type(sync), '_settings', return_value={
                'spreadsheet_id': 'sheet', 'gid': 1, 'credentials': '{}',
                'approval_scope': 'L,P'}), \
                patch.object(type(sync), '_access_token', return_value='token'), \
                patch.object(type(sync), '_sheet_title', return_value='Portfolio'), \
                patch.object(type(sync), '_red_header_columns', return_value={'L', 'P', 'T'}), \
                patch.object(type(sync), '_validation_options_by_cell', return_value=validations):
            options = sync._approved_dropdown_options()
        self.assertEqual(options['sheet_stage']['options'], ['Live', 'Cancelled'])
        self.assertEqual(options['sheet_active_implemented_modules']['options'],
                         ['Sales', 'Accounting'])
        self.assertTrue(options['sheet_active_implemented_modules']['multiple'])
        self.assertFalse(options['sheet_stage']['multiple'])
        self.assertNotIn('sheet_expansion_status', options)

    def test_match_all_writes_status_to_column_a_only(self):
        sync = self.env['cs.google.sheet.sync']
        rows = [
            ['ERA CSM', 'ERA CSM phone number', 'ERA CSM email', 'Customer Name'],
            ['', '', '', 'Matched Customer'],
            ['', '', '', 'Unknown Customer'],
            ['', '', '', ''],
        ]
        with patch.object(type(sync), '_settings', return_value={
                'spreadsheet_id': 'sheet', 'gid': 1, 'credentials': '{}'}), \
                patch.object(type(sync), '_access_token', return_value='token'), \
                patch.object(type(sync), '_sheet_title', return_value='Portfolio'), \
                patch.object(type(sync), '_match_account_with_ai', side_effect=[
                    (self.account, 96, 'email'),
                    (False, 0, 'manual review required'),
                ]), \
                patch.object(type(sync), '_request', side_effect=[{'values': rows}, {}]) as request_mock:
            sync.action_match_all_sheet_customers()
        payload = request_mock.call_args_list[-1].kwargs['json']['data']
        self.assertEqual([item['range'].split('!')[1] for item in payload], ['A2', 'A3'])
        self.assertTrue(payload[0]['values'][0][0].startswith('MATCHED:'))
        self.assertEqual(payload[1]['values'][0][0], 'UNMATCHED')

    def test_complete_google_sheet_link_extracts_id_and_gid(self):
        settings = self.env['res.config.settings']
        spreadsheet_id, gid = settings._parse_google_sheet_url(
            'https://docs.google.com/spreadsheets/d/abc_DEF-123/edit?gid=1481647876#gid=1481647876')
        self.assertEqual(spreadsheet_id, 'abc_DEF-123')
        self.assertEqual(gid, 1481647876)

    def test_google_sheet_link_requires_spreadsheet_url(self):
        settings = self.env['res.config.settings']
        with self.assertRaises(UserError):
            settings._parse_google_sheet_url('https://example.com/not-a-sheet')
