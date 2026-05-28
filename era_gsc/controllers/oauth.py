"""ERA GSC — OAuth 2.0 callback.

The "Connect" button on ``era.gsc.account`` issues an ``ir.actions.act_url``
that points the admin at Google's consent screen. Google redirects back
to ``/era_gsc/oauth/callback?code=...&state=<account_id>:<token>``; we
exchange the code for tokens, store them on the account, discover the
user's GSC properties, and bounce the admin back to the account form.
"""
import logging

from odoo import http
from odoo.http import request

from ..models.gsc_client import GscClient

_logger = logging.getLogger(__name__)


def _icp(key, default=''):
    return request.env['ir.config_parameter'].sudo().get_param(key, default)


class EraGscOAuth(http.Controller):

    @http.route('/era_gsc/oauth/callback', type='http', auth='user',
                website=False, csrf=False)
    def callback(self, code=None, state=None, error=None, **kw):
        if error or not code or not state or ':' not in state:
            return request.render('http_routing.http_error', {
                'status_code': 'OAuth Error',
                'status_message': (error or 'Missing code/state — '
                                   'cancel and try again.'),
            }, status=400)

        try:
            account_id = int(state.split(':', 1)[0])
        except (TypeError, ValueError):
            return request.render('http_routing.http_error', {
                'status_code': 'OAuth Error',
                'status_message': 'Malformed state.',
            }, status=400)

        Account = request.env['era.gsc.account'].sudo()
        account = Account.browse(account_id).exists()
        if not account:
            return request.render('http_routing.http_error', {
                'status_code': 'OAuth Error',
                'status_message': 'Account not found.',
            }, status=404)

        client_id = _icp('era_gsc.client_id')
        client_secret = _icp('era_gsc.client_secret')
        if not (client_id and client_secret):
            return request.render('http_routing.http_error', {
                'status_code': 'OAuth Error',
                'status_message': 'GSC client id/secret are not configured.',
            }, status=500)

        redirect_uri = account._oauth_redirect_uri()
        try:
            payload = GscClient.exchange_code(
                client_id, client_secret, code, redirect_uri)
        except Exception as exc:  # noqa: BLE001
            _logger.exception('GSC token exchange failed')
            account.write({'state': 'error', 'last_error': str(exc)[:1000]})
            return request.render('http_routing.http_error', {
                'status_code': 'OAuth Error',
                'status_message': 'Token exchange failed: %s' % exc,
            }, status=502)

        account._write_tokens(payload)
        # Best-effort: discover sites immediately so the admin sees them.
        try:
            account.action_discover_sites()
        except Exception as exc:  # noqa: BLE001
            _logger.warning('GSC discover_sites after auth failed: %s', exc)

        # Bounce the admin back to the account form (legacy /web URL form
        # is the most portable across 17/18/19).
        return request.redirect(
            '/web#model=era.gsc.account&id=%d&view_type=form' % account.id)
