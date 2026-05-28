"""Thin REST wrapper around the Google Search Console + OAuth2 APIs.

Pure-Python, no Odoo deps — so it stays unit-testable by mocking ``requests``.
The Odoo models import ``GscClient`` and call its methods.

References:
  https://developers.google.com/identity/protocols/oauth2/web-server
  https://developers.google.com/webmaster-tools/v1/searchanalytics/query
"""
import logging

import requests

_logger = logging.getLogger(__name__)

OAUTH_AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
OAUTH_TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'
SITES_URL = 'https://www.googleapis.com/webmasters/v3/sites'
SEARCH_ANALYTICS_URL = (
    'https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query')

_TIMEOUT = 30  # seconds


class GscClient:
    """Stateless: every call takes the credentials it needs.

    The Odoo layer is the owner of token storage and refresh scheduling.
    """

    @staticmethod
    def authorize_url(client_id, redirect_uri, state):
        """Build the URL to redirect the admin to for consent."""
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': SCOPE,
            'access_type': 'offline',   # we want a refresh_token
            'prompt': 'consent',        # ensure a refresh_token is returned
            'state': state,
        }
        return OAUTH_AUTHORIZE_URL + '?' + '&'.join(
            '%s=%s' % (k, requests.utils.quote(str(v), safe=''))
            for k, v in params.items())

    @staticmethod
    def exchange_code(client_id, client_secret, code, redirect_uri):
        """Trade an authorization code for {access_token, refresh_token, expires_in}."""
        r = requests.post(OAUTH_TOKEN_URL, data={
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri,
        }, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def refresh_access_token(client_id, client_secret, refresh_token):
        """Trade a refresh_token for a fresh {access_token, expires_in}."""
        r = requests.post(OAUTH_TOKEN_URL, data={
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def list_sites(access_token):
        """List the GSC properties the authorized user can access."""
        r = requests.get(SITES_URL, headers={
            'Authorization': 'Bearer %s' % access_token,
        }, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json().get('siteEntry', [])

    @staticmethod
    def search_analytics(access_token, site_url, start_date, end_date,
                         dimensions=('query',), row_limit=1000):
        """Pull search-analytics rows for one site between two dates.

        :param start_date: 'YYYY-MM-DD'
        :param end_date:   'YYYY-MM-DD'
        :param dimensions: tuple of dimension keys to group by (e.g.
                           ('query',), ('page',), ('date', 'query'))
        :returns: list of row dicts: {keys: [...], clicks, impressions,
                  ctr, position}
        """
        url = SEARCH_ANALYTICS_URL.format(
            site=requests.utils.quote(site_url, safe=''))
        r = requests.post(url, headers={
            'Authorization': 'Bearer %s' % access_token,
            'Content-Type': 'application/json',
        }, json={
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': list(dimensions),
            'rowLimit': row_limit,
        }, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json().get('rows', [])
