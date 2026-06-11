"""Tests for era_seo_suite.

HTTP is fully mocked (``GscClient`` static methods are patched), so the
tests run without any network access or OAuth credentials.
"""
from datetime import date, timedelta
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_seo_suite.models.gsc_client import GscClient
from odoo.addons.era_seo_suite.models.gsc_site import EraGscSite

# Per-day pull contract: each request covers exactly ONE day with
# dimensions=('query',), so 'keys' holds just the query string.
_ROWS = [
    {'keys': ['cloud accounting saudi'],
     'clicks': 12, 'impressions': 240, 'ctr': 0.05, 'position': 3.2},
    {'keys': ['zatca odoo'],
     'clicks': 8, 'impressions': 120, 'ctr': 0.066, 'position': 5.5},
]


def _fake_search_analytics(token, site_url, start_date, end_date, **kwargs):
    """Honors the day-by-day pull contract: every request must span exactly
    one day; data exists for the freshest reliable day (today - lag) only,
    like a site that ranked on a single day of the window."""
    assert start_date == end_date, \
        'per-day pull contract violated: %s..%s' % (start_date, end_date)
    anchor = date.today() - timedelta(days=EraGscSite._GSC_LAG_DAYS)
    if start_date == anchor.isoformat() and not kwargs.get('start_row'):
        return list(_ROWS)
    return []


@tagged('post_install', '-at_install')
class TestGsc(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env['era.gsc.account']
        cls.Site = cls.env['era.gsc.site']
        cls.Query = cls.env['era.gsc.query']
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('era_seo_suite.client_id', 'test-client-id')
        ICP.set_param('era_seo_suite.client_secret', 'test-client-secret')
        ICP.set_param('era_seo_suite.pull_window_days', '7')

    def _connected_account(self):
        account = self.Account.create({'name': 'Test Account'})
        # Pretend OAuth succeeded.
        account._write_tokens({
            'access_token': 'AT-FRESH',
            'refresh_token': 'RT-LONG-LIVED',
            'expires_in': 3600,
        }, email='admin@example.com')
        return account

    # --- OAuth + token plumbing ----------------------------------------------

    def test_write_tokens_sets_state_connected(self):
        a = self._connected_account()
        self.assertEqual(a.state, 'connected')
        self.assertEqual(a.email, 'admin@example.com')
        self.assertEqual(a.access_token, 'AT-FRESH')
        self.assertEqual(a.refresh_token, 'RT-LONG-LIVED')

    def test_access_token_refresh_when_expired(self):
        a = self._connected_account()
        # Force expiry into the past.
        a.token_expires_at = date(2000, 1, 1)
        with patch.object(GscClient, 'refresh_access_token',
                          return_value={'access_token': 'AT-REFRESHED',
                                        'expires_in': 3600}):
            token = a._get_valid_access_token()
        self.assertEqual(token, 'AT-REFRESHED')
        a.invalidate_recordset()
        self.assertEqual(a.access_token, 'AT-REFRESHED')

    # --- Site discovery ------------------------------------------------------

    def test_discover_sites_upserts(self):
        a = self._connected_account()
        with patch.object(GscClient, 'list_sites', return_value=[
            {'siteUrl': 'https://era.net.sa/', 'permissionLevel': 'siteOwner'},
            {'siteUrl': 'sc-domain:era.net.sa', 'permissionLevel': 'siteFullUser'},
        ]):
            a.action_discover_sites()
        self.assertEqual(len(a.site_ids), 2)
        urls = a.site_ids.mapped('name')
        self.assertIn('https://era.net.sa/', urls)
        self.assertIn('sc-domain:era.net.sa', urls)

    # --- Search analytics pull ----------------------------------------------

    def test_pull_now_upserts_queries(self):
        a = self._connected_account()
        site = self.Site.create({
            'account_id': a.id,
            'name': 'https://era.net.sa/',
        })
        with patch.object(GscClient, 'search_analytics',
                          side_effect=_fake_search_analytics):
            site.action_pull_now()
        rows = self.Query.search([('site_id', '=', site.id)])
        self.assertEqual(len(rows), 2)
        first = rows.filtered(lambda r: r.query == 'cloud accounting saudi')
        self.assertEqual(first.clicks, 12)
        self.assertEqual(first.impressions, 240)
        self.assertAlmostEqual(first.position, 3.2, places=2)
        self.assertTrue(site.last_pull_date)

    def test_pull_is_idempotent(self):
        """Re-pulling the same window updates instead of duplicating."""
        a = self._connected_account()
        site = self.Site.create({
            'account_id': a.id,
            'name': 'https://era.net.sa/',
        })
        with patch.object(GscClient, 'search_analytics',
                          side_effect=_fake_search_analytics):
            site.action_pull_now()
            site.action_pull_now()
        rows = self.Query.search([('site_id', '=', site.id)])
        self.assertEqual(len(rows), 2)

    def test_pull_failure_marks_account_error(self):
        a = self._connected_account()
        site = self.Site.create({
            'account_id': a.id,
            'name': 'https://era.net.sa/',
        })
        from odoo.exceptions import UserError
        # Plain try/except, NOT self.assertRaises: Odoo's assertRaises wraps
        # the block in a savepoint and rolls it back when the exception
        # fires, which would undo the very state='error' write we assert on.
        raised = None
        with patch.object(GscClient, 'search_analytics',
                          side_effect=RuntimeError('boom')):
            try:
                site.action_pull_now()
            except UserError as exc:
                raised = exc
        self.assertIsNotNone(raised, 'pull failure must raise UserError')
        self.assertIn('boom', str(raised))
        a.invalidate_recordset()
        self.assertEqual(a.state, 'error')
        self.assertIn('boom', a.last_error or '')

    # --- Authorize URL -------------------------------------------------------

    def test_authorize_url_contains_required_params(self):
        url = GscClient.authorize_url(
            'cid', 'https://x/era_gsc/oauth/callback', 'state-1')
        for needle in ('client_id=cid', 'response_type=code',
                       'access_type=offline', 'prompt=consent',
                       'state=state-1', 'scope='):
            self.assertIn(needle, url)
