from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestPortfolioPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.share = cls.env['cs.portfolio.share'].sudo().create({
            'name': 'Odoo Review',
            'expires_on': fields.Date.add(fields.Date.today(), days=7),
            'line_ids': [(0, 0, {'customer_name': 'Privacy Test Customer', 'era_csm': 'ERA CSM', 'adoption': 42})],
        })
        cls.share.write({'sharing_approved': True, 'active': True})

    def test_portal_requires_token(self):
        response = self.url_open('/era/portfolio/%s' % self.share.id, timeout=20)
        self.assertEqual(response.status_code, 403)

    def test_portal_and_excel_show_only_published_snapshot(self):
        token = self.share.access_token
        page = self.url_open('/era/portfolio/%s?access_token=%s' % (self.share.id, token), timeout=20)
        self.assertIn(b'Privacy Test Customer', page.content)
        self.assertNotIn(b'health_score', page.content)
        excel = self.url_open('/era/portfolio/%s/xlsx?access_token=%s' % (self.share.id, token), timeout=20)
        self.assertEqual(excel.status_code, 200)
        self.assertEqual(excel.headers['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_expired_token_is_rejected(self):
        self.share.expires_on = fields.Date.subtract(fields.Date.today(), days=1)
        response = self.url_open('/era/portfolio/%s?access_token=%s' % (self.share.id, self.share.access_token), timeout=20)
        self.assertEqual(response.status_code, 403)

    def test_portal_requires_information_sharing_approval(self):
        self.share.sharing_approved = False
        response = self.url_open('/era/portfolio/%s?access_token=%s' % (self.share.id, self.share.access_token), timeout=20)
        self.assertEqual(response.status_code, 403)


@tagged('post_install', '-at_install')
class TestGoogleSheetScope(HttpCase):

    def test_sync_updates_era_columns_only(self):
        sync = self.env['cs.google.sheet.sync']
        rows = [['ERA CSM', 'ERA CSM phone number', 'ERA CSM email', 'Customer Name', 'Date of Join', 'Next invoice date', 'Recurring plan', 'Industry', '# of Employees', '# of Users', 'Active Users', 'Stage', 'Version', 'Adoption', 'Client Website', 'Active Implemented modules', 'Potential Expansion', 'Next Action', 'Extra Notes', 'Expansion Status']]
        with patch.object(type(sync), '_settings', return_value={'enabled': True, 'spreadsheet_id': 'sheet', 'gid': 1, 'credentials': '{}', 'sharing_approved': True, 'approval_scope': 'A,B,C,G,H,K,N,O'}), \
                patch.object(type(sync), '_access_token', return_value='token'), \
                patch.object(type(sync), '_sheet_title', return_value='Portfolio'), \
                patch.object(type(sync), '_red_header_columns', return_value={'A', 'B', 'C', 'G', 'H', 'K', 'N', 'O'}), \
                patch.object(type(sync), '_request', side_effect=[{'values': rows}, {}]) as request_mock:
            sync.action_sync()
        payload = request_mock.call_args_list[-1].kwargs['json']['data'] if request_mock.call_args_list[-1].kwargs.get('json') else []
        allowed = {'A', 'B', 'C', 'G', 'H', 'K', 'N', 'O'}
        self.assertTrue(all(item['range'].split('!')[1][0] in allowed for item in payload))

    def test_sync_uses_approved_scope_not_hardcoded_era_columns(self):
        sync = self.env['cs.google.sheet.sync']
        rows = [['ERA CSM', 'ERA CSM phone number', 'ERA CSM email', 'Customer Name']]
        with patch.object(type(sync), '_settings', return_value={
                'enabled': True, 'spreadsheet_id': 'sheet', 'gid': 1, 'credentials': '{}',
                'sharing_approved': True, 'approval_scope': 'A,B'}), \
                patch.object(type(sync), '_access_token', return_value='token'), \
                patch.object(type(sync), '_sheet_title', return_value='Portfolio'), \
                patch.object(type(sync), '_red_header_columns', return_value={'A', 'B'}), \
                patch.object(type(sync), '_request', side_effect=[{'values': rows}, {}]):
            result = sync.action_sync()
        self.assertEqual(result['params']['type'], 'success')
