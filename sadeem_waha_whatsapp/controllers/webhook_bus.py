import json
import logging

from odoo import http
from odoo.http import request

from odoo.addons.sadeem_waha_whatsapp.controllers.webhook_controller import (
    WhatsAppWebhookController,
)

_logger = logging.getLogger(__name__)

NEW_MESSAGE = 'sadeem_waha_chat/new_message'
MESSAGE_ACK = 'sadeem_waha_chat/message_ack'
MESSAGE_REACTION = 'sadeem_waha_chat/message_reaction'


def _channel(session):
    return 'sadeem_waha_chat/session_%s' % session.id


class WahaChatWebhookBus(WhatsAppWebhookController):
    """Extend the existing webhook flow to also push to bus.bus so an open
    chat UI updates live. Does not change the base behaviour — calls super
    and adds the push."""

    # Forward-only status ordering; an ack only writes if it advances this.
    _STATUS_RANK = {
        'failed': -1, 'draft': 0, 'sent': 1, 'delivered': 2, 'read': 3,
    }

    @http.route('/waha/whatsapp/webhook', type='http', auth='none', csrf=False, methods=['GET', 'POST'])
    def whatsapp_webhook(self, **kwargs):
        # WAHA emits message.ack and message.reaction, which the base dispatcher
        # does not handle. Intercept those here; delegate everything else.
        if request.httprequest.method == 'POST':
            try:
                data = json.loads(request.httprequest.data or '{}')
                event = data.get('event')
                if event == 'message.reaction':
                    self._process_message_reaction(data)
                    return request.make_json_response({'status': 'success'})
                if event == 'message.ack':
                    self._process_ack_event(data)
                    return request.make_json_response({'status': 'success'})
            except Exception as e:
                _logger.error("Error processing webhook in bus override: %s", e)
        return super().whatsapp_webhook(**kwargs)

    def _process_ack_event(self, data):
        """Handle WAHA's message.ack event: update stored status + push ticks.
        ack: 1=SERVER(sent), 2=DEVICE(delivered), 3=READ, 4=PLAYED, -1=ERROR."""
        payload = data.get('payload', {}) or {}
        msg_id = payload.get('id')
        ack = payload.get('ack')
        if not msg_id:
            return
        session = request.env['sadeem.waha.session'].sudo().search(
            [('session_id', '=', data.get('session'))], limit=1,
        )
        if not session:
            return
        status_map = {-1: 'failed', 0: 'sent', 1: 'sent', 2: 'delivered', 3: 'read', 4: 'read'}
        status = status_map.get(ack, 'sent')
        Message = request.env['sadeem.waha.whatsapp.message'].sudo()
        message = Message.search([('waha_message_id', '=', msg_id)], limit=1)
        # WAHA's ack id often differs in address form (@lid vs @c.us) from the id
        # stored at send time, but the trailing hash is stable. Match on it.
        msg_hash = self._id_hash(msg_id)
        if not message and msg_hash:
            message = Message.search(
                [('waha_message_id', '=like', '%_' + msg_hash)], limit=1,
            )
        # Only write when the status actually advances (or is a genuine error).
        # WAHA fires the same ack several times at once (and acks can arrive out
        # of order), so blind writes race on the same row -> "could not serialize
        # access". A monotonic guard makes duplicates/regressions no-ops, killing
        # the race; 'failed' is always allowed through since errors matter.
        if message and message.status != status and (
            status == 'failed'
            or self._STATUS_RANK.get(status, 0) > self._STATUS_RANK.get(message.status, 0)
        ):
            message.status = status
        chat_id = message.chat_id if message else (payload.get('from') or '')
        request.env['bus.bus'].sudo()._sendone(
            _channel(session),
            MESSAGE_ACK,
            {
                'chat_id': chat_id,
                'phone_number': message.phone_number if message else '',
                # Client binds ticks by this id; send the STORED id when matched
                # (that's what the bubble has), else the raw ack id.
                'waha_message_id': message.waha_message_id if message else msg_id,
                'msg_hash': msg_hash,
                'ack': ack,
                'status': status,
            },
        )

    @staticmethod
    def _id_hash(waha_id):
        """The trailing hash of a WAHA message id (stable across address forms).
        'true_164...@lid_3EB0AB' -> '3EB0AB'."""
        return (waha_id or '').rsplit('_', 1)[-1] if waha_id else ''

    def _process_message_reaction(self, data):
        payload = data.get('payload', {}) or {}
        reaction = payload.get('reaction', {}) or {}
        target_id = reaction.get('messageId')
        if not target_id:
            return
        session = request.env['sadeem.waha.session'].sudo().search(
            [('session_id', '=', data.get('session'))], limit=1,
        )
        if not session:
            return
        # Derive chat_id from the reacted message if we have it stored;
        # fall back to the reactor's address.
        message = request.env['sadeem.waha.whatsapp.message'].sudo().search(
            [('waha_message_id', '=', target_id)], limit=1,
        )
        # Persist so the reaction survives a page refresh (NOWEB doesn't return
        # reactions in the message list).
        if message:
            message.reaction = reaction.get('text', '')
        chat_id = message.chat_id if message else (payload.get('from') or '')
        request.env['bus.bus'].sudo()._sendone(
            _channel(session),
            MESSAGE_REACTION,
            {
                'chat_id': chat_id,
                'message_id': target_id,
                'reaction': reaction.get('text', ''),  # '' means removed
                'from': payload.get('from', ''),
                'fromMe': bool(payload.get('fromMe')),
            },
        )

    def _notify_new_message(self, message):
        # Preserve the base mail-notification behaviour.
        super()._notify_new_message(message)
        # Duplicate webhooks lose the ON CONFLICT race and arrive here with an
        # empty recordset — nothing to push.
        if not message:
            return
        try:
            _logger.info(
                "NEW_MESSAGE push -> channel=%s chat_id=%s phone=%s",
                _channel(message.session_id), message.chat_id, message.phone_number,
            )
            request.env['bus.bus'].sudo()._sendone(
                _channel(message.session_id),
                NEW_MESSAGE,
                {
                    # Raw chat_id may be an @lid; phone is the resolved number the
                    # open UI keys chats by. The client matches on either.
                    'chat_id': message.chat_id,
                    'phone_number': message.phone_number or '',
                    'text': message.text or '',
                    'direction': message.direction,
                    'message_type': message.message_type,
                    'status': message.status,
                    'waha_message_id': message.waha_message_id or '',
                    'partner_name': message.partner_id.name if message.partner_id else message.phone_number,
                    'create_date': message.create_date.strftime('%Y-%m-%d %H:%M:%S') if message.create_date else '',
                },
            )
        except Exception as e:
            _logger.error("Error pushing new_message to bus: %s", e)

    def _process_message_status(self, data):
        # Let the base update message.status first.
        super()._process_message_status(data)
        try:
            payload = data.get('payload', {}) or {}
            message_id = payload.get('id')
            if not message_id:
                return
            message = request.env['sadeem.waha.whatsapp.message'].sudo().search(
                [('waha_message_id', '=', message_id)], limit=1,
            )
            if not message:
                return
            request.env['bus.bus'].sudo()._sendone(
                _channel(message.session_id),
                MESSAGE_ACK,
                {
                    'chat_id': message.chat_id,
                    'waha_message_id': message_id,
                    'ack': payload.get('ack'),
                    'status': message.status,
                },
            )
        except Exception as e:
            _logger.error("Error pushing message_ack to bus: %s", e)
