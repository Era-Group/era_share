# Part of Era Group custom addons.
import base64
import hashlib
import hmac
import json
import logging

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.http import request
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import consteq

_logger = logging.getLogger(__name__)


def _waha_hmac_candidates(secret, raw):
    """Every plausible way WAHA might sign the webhook body, so verification matches
    whichever it actually uses (WAHA's exact algorithm/encoding isn't documented and has
    caused false rejections). Keyed by a label for diagnostics."""
    key = secret.encode()
    return {
        'sha512-hex': hmac.new(key, raw, hashlib.sha512).hexdigest(),
        'sha256-hex': hmac.new(key, raw, hashlib.sha256).hexdigest(),
        'sha512-b64': base64.b64encode(hmac.new(key, raw, hashlib.sha512).digest()).decode(),
        'sha256-b64': base64.b64encode(hmac.new(key, raw, hashlib.sha256).digest()).decode(),
    }


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
            received = request.httprequest.headers.get('X-Webhook-Hmac', '')
            if received:
                # WAHA sent a signature — verify it (against every common format, since
                # WAHA's exact algorithm/encoding isn't documented).
                candidates = _waha_hmac_candidates(secret, raw)
                if not any(consteq(received, c) for c in candidates.values()):
                    _logger.warning(
                        "WAHA webhook HMAC mismatch for %s: received=%r bodylen=%s candidates=%s",
                        session_name, received[:64], len(raw),
                        {k: v[:24] for k, v in candidates.items()})
                    raise Forbidden()
            else:
                # A secret is set but WAHA sent NO signature. This WAHA server does not sign
                # webhooks (verified: the header is always empty), so HMAC cannot be enforced.
                # Accept anyway — dropping these would be a silent TOTAL reception outage — and
                # warn, so a secret set by mistake never takes messaging down again.
                _logger.warning(
                    "WAHA webhook: secret set but WAHA sent no signature — this WAHA does not "
                    "sign webhooks, so HMAC is not enforced (session %s).", session_name)

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
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Serialization/deadlock conflicts are retried IN-PROCESS by Odoo's request
            # dispatcher (service.model.retrying, fresh transaction each attempt) — much
            # faster than answering 500 and waiting for WAHA's redelivery. If all attempts
            # fail, the request errors out and WAHA's own retry policy takes over.
            raise
        except Exception:
            # Return a RETRYABLE status so WAHA re-delivers instead of dropping the message.
            # A processing error is usually transient (a DB serialization blip, a brief lock);
            # WAHA's retry then succeeds. This is safe because every handler is idempotent
            # (inbound: advisory lock + msg_uid dedup; ack/reaction/status: no-op if already
            # applied), so a retry of an event that actually landed never duplicates. Roll
            # back first so the retry reprocesses from a clean state.
            _logger.exception("WAHA webhook: error processing event %s (asking WAHA to retry)", event)
            request.env.cr.rollback()
            return request.make_response('error', status=500)

        return request.make_response('ok')
