# -*- coding: utf-8 -*-
"""Sembly Custom Automation (outbound webhook) receiver.

This is the ONLY channel that can deliver a transcript, a recording link or
participant emails — Sembly's MCP output schema carries none of them.

Authentication is an unguessable path token compared with ``consteq``, because
Sembly's published automation schema defines no signature header. A
``_verify_signature`` hook is in place so HMAC can be enforced the day Sembly
adds one, without reshaping the controller.
"""
import json
import logging
import threading
import time

from odoo import http
from odoo.http import request
from odoo.tools import consteq

from ..models.sembly_meeting import WEBHOOK_ID_KEYS

_logger = logging.getLogger(__name__)

# Fixed-window per-IP rate limit (CLAUDE.md rule 13).
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 120
_rate_lock = threading.Lock()
_rate_buckets = {}  # ip -> [window_start, count, warned]


def _rate_limited(ip):
    """True when this IP has exceeded the window. One log line per window."""
    now = time.time()
    with _rate_lock:
        window_start, count, warned = _rate_buckets.get(ip, (now, 0, False))
        if now - window_start >= RATE_LIMIT_WINDOW:
            window_start, count, warned = now, 0, False
        count += 1
        limited = count > RATE_LIMIT_MAX
        should_warn = limited and not warned
        _rate_buckets[ip] = (window_start, count, warned or limited)
        # Opportunistic cleanup so the dict cannot grow without bound.
        if len(_rate_buckets) > 1000:
            for key in [k for k, v in _rate_buckets.items()
                        if now - v[0] >= RATE_LIMIT_WINDOW * 2]:
                _rate_buckets.pop(key, None)
    if should_warn:
        _logger.warning("Sembly webhook: rate limit hit for %s (>%s req/%ss)",
                        ip, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW)
    return limited


class SemblyWebhook(http.Controller):

    def _verify_signature(self, raw, headers):
        """Hook for a future Sembly HMAC header. Today Sembly signs nothing, so
        this is a no-op and the path token is the whole authentication."""
        return True

    @http.route('/sembly/webhook/<string:token>', type='http', auth='public',
                csrf=False, methods=['GET', 'POST'], save_session=False)
    def sembly_webhook(self, token, **kwargs):
        remote_ip = request.httprequest.remote_addr or '-'

        # GET makes Sembly's "Test" button and a manual curl check succeed.
        if request.httprequest.method == 'GET':
            return request.make_response('sembly webhook active')

        if _rate_limited(remote_ip):
            return request.make_response('too many requests', status=429)

        icp = request.env['ir.config_parameter'].sudo()
        expected = icp.get_param('sembly.webhook_token') or ''
        if not expected or not consteq(token or '', expected):
            _logger.warning("Sembly webhook: bad path token from %s", remote_ip)
            request.env['sembly.sync.log'].sudo()._log(
                'webhook', 'auth', 'error', "Rejected: bad path token",
                remote_ip=remote_ip)
            return request.make_response('forbidden', status=403)

        try:
            max_bytes = int(icp.get_param('sembly.webhook_max_bytes', 1048576))
        except (TypeError, ValueError):
            max_bytes = 1048576

        raw = request.httprequest.get_data() or b''
        if len(raw) > max_bytes:
            _logger.warning("Sembly webhook: payload too large (%s bytes) from %s",
                            len(raw), remote_ip)
            return request.make_response('payload too large', status=413)

        if not self._verify_signature(raw, request.httprequest.headers):
            return request.make_response('forbidden', status=403)

        try:
            payload = json.loads(raw or b'{}')
        except (ValueError, TypeError):
            return request.make_response('bad request', status=400)
        if not isinstance(payload, dict):
            return request.make_response('bad request', status=400)

        started = time.time()
        try:
            kind = self._discriminate(payload)
            meeting = request.env['sembly.meeting'].sudo()._upsert_from_webhook(
                payload, kind)
            if request.env['sembly.meeting'].sudo()._is_webhook_test_payload(payload):
                request.env['sembly.sync.log'].sudo()._log(
                    'webhook', kind, 'ok',
                    "Sembly test payload accepted (no meeting created). Fields "
                    "sent: %s" % ", ".join(sorted(payload)),
                    remote_ip=remote_ip)
                return request.make_response('ok: test payload')

            if not meeting:
                # Sembly publishes no schema for these payloads, so "no
                # meeting_id" on its own is a dead end — it cannot be told from
                # a Sembly test ping, a renamed field, or a nested body. Record
                # the top-level KEYS (never the values: the body carries the
                # transcript and participant emails) so the next hit identifies
                # the shape instead of being rejected silently again.
                request.env['sembly.sync.log'].sudo()._log(
                    'webhook', kind, 'error',
                    "No recognised meeting id. Tried %s. Payload keys were: %s"
                    % (", ".join(WEBHOOK_ID_KEYS),
                       ", ".join(sorted(payload)) or '(empty body)'),
                    remote_ip=remote_ip)
                return request.make_response('ignored: no meeting_id', status=200)

            request.env['sembly.sync.log'].sudo()._log(
                'webhook', kind, 'ok', "meeting %s" % meeting.sembly_meeting_id,
                meeting_count=1, duration_ms=int((time.time() - started) * 1000),
                remote_ip=remote_ip)
            # AI matching is NOT run inline — the 15-minute matcher cron picks
            # the record up, so the HTTP response stays fast.
            return request.make_response('ok')
        except Exception as exc:  # noqa: BLE001 - answer 500 so Sembly retries
            _logger.exception("Sembly webhook processing failed")
            request.env['sembly.sync.log'].sudo()._log(
                'webhook', 'process', 'error', str(exc), remote_ip=remote_ip)
            return request.make_response('error', status=500)

    @staticmethod
    def _discriminate(payload):
        """Sembly's automations are told apart by field presence, exactly as
        the published schema defines them."""
        if payload.get('meeting_transcription'):
            return 'transcription'
        if payload.get('meeting_notes'):
            return 'notes'
        if payload.get('item_id') or payload.get('item_header_text'):
            return 'task'
        return 'notes'
