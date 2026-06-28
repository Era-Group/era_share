from odoo import http, _
from odoo.http import request
import json
import logging
import base64

_logger = logging.getLogger(__name__)


class WhatsAppWebhookController(http.Controller):

    @http.route('/waha/whatsapp/webhook', type='http', auth='none', csrf=False, methods=['GET', 'POST'])
    def whatsapp_webhook(self, **kwargs):
        """Handle incoming webhook from WAHA"""
        if request.httprequest.method == 'GET':
            return "WhatsApp Webhook is active"

        try:
            data = json.loads(request.httprequest.data or '{}')
            _logger.info(f"Received WhatsApp webhook: {json.dumps(data, indent=2)}")

            if not data:
                return request.make_json_response({'status': 'error', 'message': 'No data received'}, status=400)

            event = data.get('event')

            # 'message' fires only for incoming; 'message.any' also covers our
            # own messages sent from another device (fromMe). Handle both.
            if event in ('message', 'message.any'):
                self._process_incoming_message(data)
            elif event == 'message.status':
                self._process_message_status(data)
            elif event == 'session.status':
                self._process_session_status(data)
            elif event == 'connection.update':
                self._process_connection_update(data)
            else:
                _logger.info(f"Unhandled webhook event: {event}")

            return request.make_json_response({'status': 'success', 'message': f'Event {event} processed'})

        except Exception as e:
            _logger.error(f"Error processing WhatsApp webhook: {e}")
            return request.make_json_response({'status': 'error', 'message': str(e)}, status=500)

    def _process_incoming_message(self, data):
        """Process incoming WhatsApp message"""
        try:
            session_name = data.get('session')
            payload = data.get('payload', {})

            from_me = bool(payload.get('fromMe'))
            # NOWEB sets 'from' to the chat counterpart for both directions
            # (the recipient on outgoing, the sender on incoming) — there is no
            # 'to' field. So 'from' is always the chat contact; fromMe only
            # decides direction.
            from_contact = payload.get('from', '')

            # ===== Skip status@broadcast and system messages =====
            if not from_contact or from_contact in ('status@broadcast', ''):
                _logger.debug(f"Skipping system/broadcast message: {from_contact}")
                return
            # ======================================================

            session = request.env['sadeem.waha.session'].sudo().search([
                ('session_id', '=', session_name)
            ], limit=1)

            if not session:
                _logger.debug(f"Session {session_name} not found")
                return

            chat_id = from_contact
            is_lid = '@lid' in from_contact

            # For @lid contacts, resolve to real phone first before fetching contact info.
            # The contact info 'number' field just echoes the LID numeric part, not a real phone.
            phone_number = ''
            if is_lid:
                raw_phone = self._resolve_lid_to_phone(session, from_contact)
                phone_number = self._sanitize_phone(raw_phone)
                _logger.info(f"Resolved LID {from_contact} -> phone_number={phone_number!r}")

            # Fetch contact info (used for name, profile picture, etc.)
            contact_info = self._fetch_contact_info(session, from_contact)

            # For non-LID contacts, extract phone from contact info
            if not is_lid and contact_info:
                phone_number = self._sanitize_phone(contact_info.get('number', ''))

            # Fallback: NOWEB embeds the real JID in _data.key.remoteJidAlt
            if not phone_number and is_lid:
                alt_jid = payload.get('_data', {}).get('key', {}).get('remoteJidAlt', '')
                if alt_jid:
                    phone_number = self._sanitize_phone(alt_jid)
                    if phone_number:
                        _logger.info(f"Resolved LID {from_contact} via remoteJidAlt -> {phone_number}")

            # Final fallback: parse directly from the JID
            if not phone_number:
                if '@c.us' in from_contact or '@s.whatsapp.net' in from_contact:
                    phone_number = self._sanitize_phone(from_contact)
                elif is_lid:
                    _logger.debug(f"Could not resolve LID {from_contact} to a real phone number")

            # WEBJS engine sets hasMedia=true but leaves payload.type absent;
            # the actual type lives in payload._data.type.  Fall back to that,
            # then use hasMedia as the final signal.
            message_type = (
                payload.get('type')
                or payload.get('_data', {}).get('type')
                or ('image' if payload.get('hasMedia') else 'text')
            )
            text = payload.get('body', '')

            attachment_id = None
            if message_type in ['image', 'document', 'audio', 'video'] or payload.get('hasMedia'):
                attachment_id = self._process_media_message(payload, session)

            partner = self._find_or_create_partner(phone_number, payload, contact_info, session)

            message_vals = {
                'session_id': session.id,
                'partner_id': partner.id if partner else False,
                'phone_number': phone_number or from_contact,
                'chat_id': chat_id,
                'text': text,
                'direction': 'outgoing' if from_me else 'incoming',
                'message_type': message_type,
                'status': 'sent' if from_me else 'delivered',
                'waha_message_id': payload.get('id'),
                'attachment_id': attachment_id.id if attachment_id else False,
            }

            message = request.env['sadeem.waha.whatsapp.message'].sudo().create(message_vals)

            _logger.info(f"Incoming message processed: {message.id} from {phone_number or from_contact}")
            self._notify_new_message(message)

        except Exception as e:
            _logger.error(f"Error processing incoming message: {e}")

    def _fetch_contact_info(self, session, contact_id):
        """Fetch contact info from WAHA API"""
        try:
            import requests as req
            url = f"{session.waha_server_url.rstrip('/')}/api/contacts"
            headers = {'Content-Type': 'application/json'}
            if session.api_key:
                headers['X-Api-Key'] = session.api_key

            # جرب الأول بالـ number بس (بدون @c.us) لأن NOWEB أحيانًا بيرفض الـ full JID
            clean_number = contact_id.replace('@c.us', '').replace('@lid', '')

            for contact_id_attempt in [clean_number, contact_id]:
                params = {'contactId': contact_id_attempt, 'session': session.session_id}
                response = req.get(url, params=params, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    _logger.info(f"Contact info fetched for {contact_id_attempt}: {data}")
                    return data
                else:
                    _logger.debug(f"Failed to fetch contact info for {contact_id_attempt}: {response.status_code}")

            return None
        except Exception as e:
            _logger.debug(f"Could not fetch contact info for {contact_id}: {e}")
            return None

    def _resolve_lid_to_phone(self, session, lid):
        """Resolve @lid to phone number using WAHA LID API"""
        try:
            import requests as req
            lid_encoded = lid.replace('@', '%40')
            url = f"{session.waha_server_url.rstrip('/')}/api/{session.session_id}/lids/{lid_encoded}"
            headers = {}
            if session.api_key:
                headers['X-Api-Key'] = session.api_key

            response = req.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                pn = data.get('pn', '')
                if pn:
                    phone = pn.replace('@c.us', '')
                    _logger.info(f"Resolved LID {lid} -> phone {phone}")
                    return phone
            _logger.debug(f"Could not resolve LID {lid} to phone number")
            return ''
        except Exception as e:
            _logger.error(f"Error resolving LID {lid}: {e}")
            return ''

    def _sanitize_phone(self, raw):
        """Strip JID suffixes and non-digit chars, return clean digits-only number.
        Returns empty string if result looks invalid (too short or too long).
        """
        if not raw:
            return ''
        # Remove common JID suffixes
        for suffix in ('@c.us', '@s.whatsapp.net', '@lid', '@g.us', '@broadcast'):
            raw = raw.replace(suffix, '')
        # Keep only digits and leading +
        raw = raw.strip()
        if raw.startswith('+'):
            digits = '+' + ''.join(c for c in raw[1:] if c.isdigit())
        else:
            digits = ''.join(c for c in raw if c.isdigit())
        # Valid international numbers: 7–15 digits (E.164 without +)
        digit_count = len(digits.lstrip('+'))
        if digit_count < 7 or digit_count > 15:
            _logger.warning(f"Suspicious phone number discarded: {digits!r} (digits={digit_count})")
            return ''
        return digits


    def _find_or_create_partner(self, phone_number, payload, contact_info=None, session=None):
        """Find existing partner or create new one with full contact details"""
        try:
            if not phone_number and not contact_info:
                return None

            Partner = request.env['res.partner'].sudo()
            partner = None
            mobile = None  # only set when we have a real phone (not group @g.us)

            if phone_number:
                # Sanitize again as a safety net, then ensure + prefix
                phone_number = self._sanitize_phone(phone_number) or phone_number
                mobile = f'+{phone_number}' if not phone_number.startswith('+') else phone_number

                # ابحث بـ phone_sanitized أولاً (ده الأدق - أودو بيطبع فيه E.164 format)
                # بعدين جرب phone كـ fallback (Odoo 19 dropped the mobile field)
                partner = Partner.search([
                    '|',
                    ('phone_sanitized', '=', mobile),
                    ('phone', '=', mobile),
                ], limit=1)

                # لو مش لاقي، جرب بدون الـ + (على حالات قديمة)
                if not partner:
                    partner = Partner.search([
                        '|',
                        ('phone_sanitized', '=', phone_number),
                        ('phone', '=', phone_number),
                    ], limit=1)

            # No real phone (e.g. group @g.us): don't invent a partner from a
            # group id — return None and let the message store without one.
            if not partner and not mobile:
                return None

            if not partner:
                # جمع الاسم من المصادر المتاحة
                name = None
                if contact_info:
                    name = (contact_info.get('name') or
                            contact_info.get('pushname') or
                            contact_info.get('shortName'))
                if not name:
                    name = (payload.get('notifyName') or
                            payload.get('pushName') or
                            payload.get('_data', {}).get('pushName') or
                            mobile or
                            'Unknown')

                partner = Partner.create({
                    'name': name,
                    'phone': mobile,
                    'is_company': False,
                })
                _logger.info(f"Created new partner: {name} (phone: {mobile})")

                # جيب profile picture
                if session and contact_info:
                    contact_id = contact_info.get('id') or f'{phone_number}@c.us'
                    if contact_id:
                        profile_pic = self._fetch_contact_profile_picture(session, contact_id)
                        if profile_pic:
                            try:
                                partner.image_1920 = profile_pic
                            except Exception:
                                pass
            else:
                _logger.info(f"Found existing partner: {partner.name} (id: {partner.id})")

            return partner

        except Exception as e:
            _logger.error(f"Error finding/creating partner: {e}")
            return None

    def _fetch_contact_profile_picture(self, session, contact_id):
        """Fetch contact profile picture from WAHA"""
        try:
            import requests as req
            import base64
            url = f"{session.waha_server_url.rstrip('/')}/api/contacts/profile-picture"
            headers = {}
            if session.api_key:
                headers['X-Api-Key'] = session.api_key

            params = {'contactId': contact_id, 'session': session.session_id}
            response = req.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                picture_url = data.get('profilePictureURL')
                if picture_url:
                    pic_response = req.get(picture_url, timeout=10)
                    if pic_response.status_code == 200:
                        return base64.b64encode(pic_response.content)
            return None
        except Exception as e:
            _logger.error(f"Error fetching profile picture for {contact_id}: {e}")
            return None

    def _process_message_status(self, data):
        """Process message status updates (sent, delivered, read)"""
        try:
            session_name = data.get('session')
            payload = data.get('payload', {})

            message_id = payload.get('id')
            status = payload.get('ack')  # 1=sent, 2=delivered, 3=read

            # تحويل الأرقام لحالات
            status_map = {
                1: 'sent',
                2: 'delivered',
                3: 'read'
            }

            new_status = status_map.get(status, 'sent')

            # البحث عن الرسالة وتحديث حالتها
            message = request.env['sadeem.waha.whatsapp.message'].sudo().search([
                ('waha_message_id', '=', message_id)
            ], limit=1)

            # Monotonic guard: WAHA fires the same message.ack ~4x at once.
            # Writing on every one causes concurrent UPDATEs on the same row
            # ("could not serialize access"). Only write when status advances,
            # so duplicate/out-of-order acks are no-ops.
            rank = {'failed': -1, 'draft': 0, 'sent': 1, 'delivered': 2, 'read': 3}
            if message and rank.get(new_status, 0) > rank.get(message.status, 0):
                message.status = new_status
                _logger.info(f"Message {message_id} status updated to {new_status}")

        except Exception as e:
            _logger.error(f"Error processing message status: {e}")

    def _process_session_status(self, data):
        """Process session status updates"""
        try:
            session_name = data.get('session')
            payload = data.get('payload', {})

            session = request.env['sadeem.waha.session'].sudo().search([
                ('session_id', '=', session_name)
            ], limit=1)

            if session:
                new_status = payload.get('status', '').lower()
                if new_status:
                    session.status = new_status

                    # تحديث معلومات الملف الشخصي
                    if 'me' in payload:
                        me_data = payload['me']
                        session.phone_number = me_data.get('id', '').replace('@c.us', '')
                        session.profile_name = me_data.get('pushName', '')

                    _logger.info(f"Session {session_name} status updated to {new_status}")

        except Exception as e:
            _logger.error(f"Error processing session status: {e}")

    def _process_connection_update(self, data):
        """Process connection status updates"""
        try:
            session_name = data.get('session')
            payload = data.get('payload', {})

            connection = payload.get('connection')
            _logger.info(f"Session {session_name} connection update: {connection}")

        except Exception as e:
            _logger.error(f"Error processing connection update: {e}")

    def _process_media_message(self, payload, session):
        """Process media messages and create attachments.

        WAHA WEBJS engine stores the decoded media in _data.body as a base64
        string. The media.url points to a WAHA file server that may not be
        reachable from Odoo's network. We therefore prefer _data.body and only
        fall back to the URL if _data.body is absent.
        """
        try:
            media_data = payload.get('media', {}) or {}
            mimetype = media_data.get('mimetype', 'application/octet-stream')
            filename = (media_data.get('filename')
                        or f"media_{payload.get('id', 'unknown')}.{mimetype.split('/')[-1]}")

            content = None

            # Primary: download full-resolution file from WAHA URL.
            # WAHA deployed remotely may still report localhost in the URL —
            # replace the host with the session's configured waha_server_url.
            url = media_data.get('url')
            if url:
                import re as _re
                import requests as req

                url = _re.sub(
                    r'^https?://[^/]+',
                    session.waha_server_url.rstrip('/'),
                    url
                )

                headers = {}
                if session.api_key:
                    headers['X-Api-Key'] = session.api_key

                _logger.info(f"Downloading media from {url}")
                response = req.get(url, headers=headers, timeout=30)
                ct = response.headers.get('Content-Type', '')
                _logger.info(f"Downloaded {len(response.content)} bytes, Content-Type: {ct}, first4: {response.content[:4]!r}")

                if response.status_code == 200 and not ct.startswith('text/html'):
                    content = response.content
                else:
                    _logger.warning(f"Media URL unusable (status={response.status_code}, ct={ct}), falling back to _data.body")

            # Fallback: use the thumbnail/preview embedded in _data.body (WEBJS).
            # This is lower resolution than the original but better than nothing.
            if not content:
                body_b64 = payload.get('_data', {}).get('body', '')
                if body_b64:
                    try:
                        content = base64.b64decode(body_b64)
                        _logger.info(f"Using _data.body fallback: {len(content)} bytes")
                    except Exception as e:
                        _logger.error(f"Failed to decode _data.body: {e}")

            if not content:
                _logger.warning("No media content available, skipping attachment")
                return None

            attachment = request.env['ir.attachment'].sudo().create({
                'name': filename,
                'raw': content,
                'mimetype': mimetype,
                'res_model': 'sadeem.waha.whatsapp.message',
            })
            _logger.info(f"Attachment created: {attachment.id} ({filename}, {len(content)} bytes)")
            return attachment

        except Exception as e:
            _logger.error(f"Error processing media message: {e}")

        return None

    def _notify_new_message(self, message):
        """Send notification for new incoming message — only if no unread notification already exists."""
        try:
            session = message.session_id
            partner_ids = session.notify_user_ids.mapped('partner_id').ids
            if not partner_ids:
                return

            Notif = request.env['mail.notification'].sudo()
            partners_to_notify = []
            for pid in partner_ids:
                unread = Notif.search_count([
                    ('res_partner_id', '=', pid),
                    ('mail_message_id.model', '=', 'sadeem.waha.whatsapp.message'),
                    ('mail_message_id.body', 'ilike', message.phone_number),
                    ('is_read', '=', False),
                ], limit=1)
                if not unread:
                    partners_to_notify.append(pid)

            if not partners_to_notify:
                return

            message.with_user(request.env.ref('base.user_root')).message_post(
                body="New WhatsApp message received from %s" % message.phone_number,
                message_type='notification',
                partner_ids=partners_to_notify,
            )

        except Exception as e:
            _logger.error("Error sending notification: %s", e)