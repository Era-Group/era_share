from odoo import http, fields
from odoo.http import request, Response
import base64
import logging
import requests as req

_logger = logging.getLogger(__name__)


class WhatsAppChatController(http.Controller):

    @http.route('/waha/chat/messages', type='json', auth='user', methods=['POST'])
    def get_chat_messages(self, chat_id, session_id, limit=100, offset=0, last_id=0):
        """Return stored messages for a chat conversation.
        If last_id is provided, only return messages with id > last_id (for polling).
        """
        try:
            domain = [('chat_id', '=', chat_id), ('session_id', '=', session_id)]
            if last_id:
                domain.append(('id', '>', last_id))

            messages = request.env['sadeem.waha.whatsapp.message'].sudo().search(
                domain,
                order='create_date asc, id asc',
                limit=limit,
                offset=offset,
            )

            result = []
            for msg in messages:
                result.append({
                    'id': msg.id,
                    'text': msg.text or '',
                    'direction': msg.direction,
                    'message_type': msg.message_type,
                    'status': msg.status,
                    'create_date': msg.create_date.strftime('%Y-%m-%d %H:%M:%S') if msg.create_date else '',
                    'partner_name': msg.partner_id.name if msg.partner_id else msg.phone_number,
                    'has_attachment': bool(msg.attachment_id),
                    'attachment_name': msg.attachment_id.name if msg.attachment_id else '',
                    'attachment_mimetype': msg.attachment_id.mimetype if msg.attachment_id else '',
                    'attachment_url': f'/web/content/{msg.attachment_id.id}' if msg.attachment_id else '',
                    'waha_message_id': msg.waha_message_id or '',
                })

            return {'success': True, 'messages': result}

        except Exception as e:
            _logger.error(f"Error fetching chat messages: {e}")
            return {'success': False, 'error': str(e)}

    @http.route('/waha/chat/messages/live', type='json', auth='user', methods=['POST'])
    def get_live_chat_messages(self, chat_id, session_id, limit=50, offset=0):
        """Fetch messages directly from WAHA API. Returns success:False on any error so the
        caller can fall back to stored messages."""
        try:
            session = request.env['sadeem.waha.session'].sudo().browse(session_id)
            if not session.exists():
                return {'success': False, 'error': 'Session not found'}

            waha_messages = session.fetch_chat_messages_from_api(chat_id, limit=limit, offset=offset)

            # NOWEB doesn't return reactions in the message list; load the ones
            # we persisted from the live message.reaction webhook.
            def _serialized(m):
                rid = m.get('id', '')
                return rid.get('_serialized') if isinstance(rid, dict) else str(rid or '')
            wanted_ids = [i for i in (_serialized(m) for m in waha_messages) if i]
            stored = request.env['sadeem.waha.whatsapp.message'].sudo().search_read(
                [('waha_message_id', 'in', wanted_ids), ('reaction', '!=', False)],
                ['waha_message_id', 'reaction'],
            )
            reactions_by_id = {r['waha_message_id']: r['reaction'] for r in stored}

            # Resolve @<number> mentions to contact names. WhatsApp writes the
            # mention as a bare number that is the @lid numeric part (see
            # contextInfo.mentionedJid e.g. '5016...@lid'). Resolve each lid to a
            # real phone via WAHA, then match an Odoo partner. Per-request cache.
            import re as _re
            mention_re = _re.compile(r'@(\d{6,})')
            Partner = request.env['res.partner'].sudo()
            names_by_number = {}

            def _ext(m):
                d = (m.get('_data', {}) or {}).get('message', {}) or {}
                ctx = (d.get('extendedTextMessage', {}) or {}).get('contextInfo', {}) or {}
                return ctx.get('mentionedJid') or []

            mentioned_jids = set()
            for m in waha_messages:
                for jid in _ext(m):
                    mentioned_jids.add(jid)

            own_lid_num = (session.phone_number or '').split('@')[0]
            for jid in mentioned_jids:
                num = jid.split('@')[0]
                if not num:
                    continue
                # @lid -> real phone (@c.us); @c.us stays as-is.
                resolved = session.resolve_lid_to_chat_id(jid) if jid.endswith('@lid') else jid
                phone = resolved.split('@')[0]
                if not phone.isdigit():
                    continue
                p = Partner.search(
                    ['|', ('phone_sanitized', '=', '+' + phone), ('phone', '=', phone)],
                    limit=1,
                )
                if p:
                    name = p.name
                elif phone == own_lid_num or resolved.split('@')[0] == own_lid_num:
                    # Mention of our own session number -> show its name.
                    name = session.name or ('+' + phone)
                else:
                    # No partner: at least show the real phone, not the lid number.
                    name = '+' + phone
                # Body shows the lid number, so key the display name by that.
                names_by_number[num] = name

            def _resolve_mentions(text):
                if not text or '@' not in text:
                    return text
                return mention_re.sub(
                    lambda mo: '@' + names_by_number.get(mo.group(1), mo.group(1)),
                    text,
                )

            result = []
            for i, msg in enumerate(waha_messages):
                body = _resolve_mentions(msg.get('body', '') or '')
                ts = msg.get('timestamp')
                create_date = ''
                if ts:
                    from datetime import datetime, timezone
                    create_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

                direction = 'outgoing' if msg.get('fromMe') else 'incoming'

                # id can be a string or an object {fromMe, remote, id, _serialized}
                raw_id = msg.get('id', '')
                if isinstance(raw_id, dict):
                    msg_id = raw_id.get('_serialized') or raw_id.get('id') or f'live_{i}'
                else:
                    msg_id = str(raw_id) if raw_id else f'live_{i}'

                has_attachment = False
                attachment_name = ''
                attachment_mimetype = ''
                attachment_url = ''
                media = msg.get('media') or {}
                if media:
                    has_attachment = True
                    attachment_mimetype = media.get('mimetype', '')
                    attachment_name = (media.get('filename')
                                       or f"media.{attachment_mimetype.split('/')[-1]}")
                    raw_url = media.get('url', '')
                    # Route through Odoo proxy so the browser never needs the WAHA API key
                    if raw_url:
                        from urllib.parse import quote
                        attachment_url = (
                            f"/waha/media/proxy"
                            f"?session_id={session_id}"
                            f"&url={quote(raw_url, safe='')}"
                            f"&filename={quote(attachment_name, safe='')}"
                        )
                    else:
                        attachment_url = ''

                reaction = reactions_by_id.get(msg_id, '')

                result.append({
                    'id': msg_id,
                    'text': body,
                    'reaction': reaction,
                    'direction': direction,
                    'message_type': msg.get('type', 'chat'),
                    'status': ('read' if msg.get('ack', 0) >= 3
                               else 'delivered' if msg.get('ack', 0) >= 2
                               else 'sent'),
                    'create_date': create_date,
                    'partner_name': (msg.get('_data', {}).get('notifyName', '')
                                     or msg.get('from', '')),
                    'has_attachment': has_attachment,
                    'attachment_name': attachment_name,
                    'attachment_mimetype': attachment_mimetype,
                    'attachment_url': attachment_url,
                    'waha_message_id': msg_id,
                })

            return {'success': True, 'messages': result}

        except Exception as e:
            _logger.error(f"Error fetching live chat messages: {e}")
            return {'success': False, 'error': str(e)}

    @http.route('/waha/media/proxy', type='http', auth='user', methods=['GET'], csrf=False)
    def proxy_waha_media(self, session_id, url, filename='file'):
        """Proxy a WAHA media URL through Odoo, injecting the API key.
        Used for live-chat attachments that require WAHA authentication.
        """
        try:
            session = request.env['sadeem.waha.session'].sudo().browse(int(session_id))
            if not session.exists():
                return Response('Session not found', status=404)

            headers = {}
            if session.api_key:
                headers['X-Api-Key'] = session.api_key

            resp = req.get(url, headers=headers, timeout=30, stream=True)
            if resp.status_code != 200:
                return Response(f'Media fetch failed: {resp.status_code}', status=resp.status_code)

            content_type = resp.headers.get('Content-Type', 'application/octet-stream')
            content_disposition = f'inline; filename="{filename}"'

            return Response(
                resp.content,
                status=200,
                headers={
                    'Content-Type': content_type,
                    'Content-Disposition': content_disposition,
                    'Content-Length': str(len(resp.content)),
                }
            )
        except Exception as e:
            _logger.error(f"Error proxying WAHA media: {e}")
            return Response('Internal error', status=500)

    @http.route('/waha/chat/send', type='json', auth='user', methods=['POST'])
    def send_chat_message(self, chat_id, session_id, text, phone_number,
                          partner_id=False, file_data=None, file_name=None, file_mimetype=None):
        """Send a text or file message from the chat view"""
        try:
            session = request.env['sadeem.waha.session'].sudo().browse(session_id)
            if not session.exists():
                return {'success': False, 'error': 'Session not found'}

            attachment = None
            message_type = 'text'

            if file_data and file_name:
                # Determine message type from mimetype
                if file_mimetype and file_mimetype.startswith('image/'):
                    message_type = 'image'
                elif file_mimetype and file_mimetype.startswith('audio/'):
                    message_type = 'voice'
                elif file_mimetype and file_mimetype.startswith('video/'):
                    message_type = 'video'
                else:
                    message_type = 'document'

                # Store attachment in Odoo
                attachment = request.env['ir.attachment'].sudo().create({
                    'name': file_name,
                    'datas': file_data,
                    'mimetype': file_mimetype or 'application/octet-stream',
                    'res_model': 'sadeem.waha.whatsapp.message',
                })

                # Send file via WAHA
                if message_type == 'voice':
                    result = session.send_voice_message(chat_id, file_data, file_name)
                else:
                    result = session.send_message(chat_id, text or '', file_data, file_name, file_mimetype)
            else:
                result = session.send_message(chat_id, text)

            msg = request.env['sadeem.waha.whatsapp.message'].sudo().create({
                'session_id': session_id,
                'partner_id': partner_id or False,
                'phone_number': phone_number,
                'chat_id': chat_id,
                'text': text or '',
                'direction': 'outgoing',
                'message_type': message_type,
                'status': 'sent',
                'waha_message_id': result.get('id') if result else False,
                'attachment_id': attachment.id if attachment else False,
            })

            return {
                'success': True,
                'message': {
                    'id': msg.id,
                    'text': msg.text,
                    'direction': 'outgoing',
                    'message_type': message_type,
                    'status': 'sent',
                    'create_date': msg.create_date.strftime('%Y-%m-%d %H:%M:%S') if msg.create_date else '',
                    'partner_name': msg.partner_id.name if msg.partner_id else msg.phone_number,
                    'has_attachment': bool(attachment),
                    'attachment_name': attachment.name if attachment else '',
                    'attachment_url': f'/web/content/{attachment.id}?download=true' if attachment else '',
                    'waha_message_id': msg.waha_message_id or '',
                }
            }

        except Exception as e:
            _logger.error(f"Error sending chat message: {e}")
            return {'success': False, 'error': str(e)}

    @http.route('/waha/chat/send_seen', type='json', auth='user', methods=['POST'])
    def send_seen(self, chat_id, session_id, message_ids, participant=None):
        try:
            session = request.env['sadeem.waha.session'].sudo().browse(session_id)
            if not session.exists():
                return {'success': False, 'error': 'Session not found'}
            session.send_seen(chat_id, message_ids, participant=participant)
            return {'success': True}
        except Exception as e:
            _logger.error(f"Error sending seen: {e}")
            return {'success': False, 'error': str(e)}

    @http.route('/waha/chat/start_typing', type='json', auth='user', methods=['POST'])
    def start_typing(self, chat_id, session_id):
        try:
            session = request.env['sadeem.waha.session'].sudo().browse(session_id)
            if not session.exists():
                return {'success': False, 'error': 'Session not found'}
            session.start_typing(chat_id)
            return {'success': True}
        except Exception as e:
            _logger.error(f"Error starting typing: {e}")
            return {'success': False, 'error': str(e)}

    @http.route('/waha/chat/stop_typing', type='json', auth='user', methods=['POST'])
    def stop_typing(self, chat_id, session_id):
        try:
            session = request.env['sadeem.waha.session'].sudo().browse(session_id)
            if not session.exists():
                return {'success': False, 'error': 'Session not found'}
            session.stop_typing(chat_id)
            return {'success': True}
        except Exception as e:
            _logger.error(f"Error stopping typing: {e}")
            return {'success': False, 'error': str(e)}
