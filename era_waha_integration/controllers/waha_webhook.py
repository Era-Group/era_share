# Part of Era Group custom addons.
import hashlib
import hmac
import json
import logging

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.http import request
from odoo.tools import consteq

_logger = logging.getLogger(__name__)


class WahaWebhook(http.Controller):

    @http.route('/era/waha/webhook', type='http', auth='public', csrf=False,
                methods=['GET', 'POST'], save_session=False)
    def waha_webhook(self, **kwargs):
        if request.httprequest.method == 'GET':
            return request.make_response('era_waha_integration webhook active')

        raw = request.httprequest.get_data() or b'{}'
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return request.make_response('bad request', status=400)

        session_name = data.get('session')
        event = data.get('event')
        payload = data.get('payload') or {}
        if not session_name:
            return request.make_response('ignored')

        account = request.env['whatsapp.account'].sudo().search([
            ('provider', '=', 'waha'), ('waha_session', '=', session_name)], limit=1)
        if not account:
            _logger.debug("WAHA webhook: no account for session %s", session_name)
            return request.make_response('ignored')

        secret = account.sudo().waha_webhook_secret
        if secret:
            signature = request.httprequest.headers.get('X-Webhook-Hmac', '')
            expected = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
            if not signature or not consteq(signature, expected):
                _logger.warning("WAHA webhook: invalid HMAC signature for session %s", session_name)
                raise Forbidden()

        try:
            if event in ('message', 'message.any'):
                account._waha_process_incoming(payload)
            elif event == 'message.ack':
                account._waha_process_ack(payload)
            elif event == 'message.reaction':
                account._waha_process_reaction(payload)
            elif event == 'session.status':
                account._waha_process_session_status(payload)
            else:
                _logger.debug("WAHA webhook: unhandled event %s", event)
        except Exception:
            _logger.exception("WAHA webhook: error processing event %s", event)

        return request.make_response('ok')
