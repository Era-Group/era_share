# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import base64
import logging

_logger = logging.getLogger(__name__)


class SadeemWahaSession(models.Model):
    _name = 'sadeem.waha.session'
    _description = 'WAHA WhatsApp Session'
    _order = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Session Name', required=True, tracking=True, help="Unique session identifier")
    waha_server_url = fields.Char('WAHA Server URL', required=True, default='http://localhost:3000', tracking=True)
    session_id = fields.Char('Session ID', tracking=True, help="WAHA session identifier")
    status = fields.Selection([
        ('stopped', 'Stopped'),
        ('starting', 'Starting'),
        ('scan_qr_code', 'Scan QR Code'),
        ('working', 'Working'),
        ('failed', 'Failed')
    ], string='Status', default='stopped', readonly=True, tracking=True)

    qr_code = fields.Text('QR Code', readonly=True)
    qr_code_image = fields.Binary('QR Code Image', readonly=True)

    phone_number = fields.Char('Phone Number', readonly=True, tracking=True)
    profile_name = fields.Char('Profile Name', readonly=True, tracking=True)
    profile_picture = fields.Binary('Profile Picture', readonly=True)

    webhook_url = fields.Char(string='Webhook URL', tracking=True, help="URL to receive incoming messages",
                              default=lambda self: self._get_default_webhook_url()
                              )
    webhook_events = fields.Char(string='Webhook Events', default='message.any,message.ack,session.status',
                                 tracking=True, help="Comma-separated list of events to receive")
    api_key = fields.Char('API Key', help="WAHA API Key for authentication")

    engine = fields.Selection([
        ('WEBJS', 'WEBJS (Browser based)'),
        ('NOWEB', 'NOWEB (WebSocket NodeJS)'),
        ('GOWS', 'GOWS (WebSocket Go)')
    ], string='Engine', default='NOWEB', tracking=True)

    active = fields.Boolean('Active', default=True, tracking=True)
    company_id = fields.Many2one('res.company', 'Company', default=lambda self: self.env.company)

    last_sync = fields.Datetime('Last Sync', readonly=True)
    error_message = fields.Text('Error Message', readonly=True)

    # Statistics (computed from actual message records)
    messages_sent = fields.Integer(
        'Messages Sent', compute='_compute_message_counts', store=False)
    messages_received = fields.Integer(
        'Messages Received', compute='_compute_message_counts', store=False)

    notify_user_ids = fields.Many2many('res.users', string='Notify Users',
                                       help="Users to notify when a new WhatsApp message is received")

    message_ids = fields.One2many(
        'sadeem.waha.whatsapp.message', 'session_id', string='Messages')

    @api.depends('message_ids.direction')
    def _compute_message_counts(self):
        for session in self:
            session.messages_sent = len(
                session.message_ids.filtered(lambda m: m.direction == 'outgoing'))
            session.messages_received = len(
                session.message_ids.filtered(lambda m: m.direction == 'incoming'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('session_id'):
                vals['session_id'] = vals.get('name', '').lower().replace(' ', '_')

        records = super().create(vals_list)

        for record in records:
            _logger.info(f"WhatsApp Session created: {record.name} (ID: {record.id})")
            record.message_post(
                body=_("WhatsApp session '%s' has been created successfully.") % record.name,
                subject=_("Session Created")
            )

        return records

    _LOCK_NAMESPACE = 0x57414841  # 'WAHA'

    def write(self, vals):
        if 'status' in vals:
            for rec in self:
                self.env.cr.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (self._LOCK_NAMESPACE, rec.id),
                )

        res = super().write(vals)

        if 'status' in vals:
            _logger.info("Session %s status changed to: %s", self.mapped('name'), vals['status'])

        if 'webhook_url' in vals:
            _logger.info("Session %s webhook URL updated to: %s", self.mapped('name'), vals['webhook_url'])

        return res

    def unlink(self):
        for record in self:
            _logger.warning(f"WhatsApp Session deleted: {record.name} (ID: {record.id})")
        return super().unlink()

    def _make_api_request(self, endpoint, method='GET', data=None, params=None):
        """Make API request to WAHA server"""
        try:
            url = f"{self.waha_server_url.rstrip('/')}/api/{endpoint.lstrip('/')}"
            headers = {'Content-Type': 'application/json'}

            if self.api_key:
                headers['X-Api-Key'] = self.api_key

            _logger.debug(f"WAHA API Request: {method} {url}")

            if method == 'GET':
                response = requests.get(url, params=params, headers=headers, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=30)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if response.status_code not in [200, 201]:
                _logger.error(f"WAHA API Error Response: {response.status_code} - {response.text}")
                try:
                    err_body = response.json()
                except Exception:
                    err_body = response.text
                raise UserError(str(err_body))

            _logger.debug(f"WAHA API Success: {method} {url} - Status: {response.status_code}")

            if response.headers.get('Content-Type', '').startswith('application/json'):
                return response.json()
            else:
                return response.content

        except requests.exceptions.RequestException as e:
            _logger.error(f"WAHA API request failed for session id={self.id}: {e}")
            self.error_message = str(e)
            raise UserError(_("Failed to connect to WAHA server: %s") % str(e))

    def action_start_session(self):
        """Start WhatsApp session"""
        self.ensure_one()

        session_id = self.id
        _logger.info(f"Starting WhatsApp session id={session_id}")

        # Flush pending ORM writes before API calls so there are no dirty
        # records when we later write status back.
        self.env.cr.flush()

        # Read field values into locals so they're safe to use in except blocks
        # where the transaction may be aborted (self.name would trigger a DB fetch).
        waha_session_id = self.session_id
        webhook_url = self.webhook_url
        webhook_events = self.webhook_events

        try:
            data = {
                'name': waha_session_id,
                'start': True,
                'config': {
                    'noweb': {
                        'store': {
                            'enabled': True,
                            'full_sync': True,
                        }
                    }
                }
            }

            if webhook_url:
                data['config']['webhooks'] = [{
                    'url': webhook_url,
                    'events': webhook_events.split(',') if webhook_events else ['message']
                }]

            # Try POST first; if session already exists on WAHA use PUT (config only, no restart)
            try:
                self._make_api_request('sessions', 'POST', data)
            except Exception as post_err:
                err_body = post_err.args[0] if post_err.args else {}
                status_code = err_body.get('statusCode') if isinstance(err_body, dict) else None
                if status_code == 422:
                    _logger.info(f"Session {waha_session_id} already exists on WAHA, updating config only")
                    put_data = {k: v for k, v in data.items() if k != 'start'}
                    self._make_api_request(f'sessions/{waha_session_id}', 'PUT', put_data)
                else:
                    raise

            self.status = 'starting'
            self.error_message = False

            _logger.info(f"Session id={session_id} started successfully")

            self.message_post(
                body=_("WhatsApp session started successfully. Please scan QR code if required."),
                subject=_("Session Started")
            )

        except Exception as e:
            _logger.error(f"Failed to start session id={session_id}: {e}")
            self.invalidate_recordset()
            self.status = 'failed'
            self.error_message = str(e)

            self.message_post(
                body=_("Failed to start session: %s") % str(e),
                subject=_("Session Start Failed")
            )

            raise UserError(_("Failed to start session: %s") % str(e))

    def action_stop_session(self):
        """Stop WhatsApp session"""
        self.ensure_one()

        session_id = self.id
        waha_session_id = self.session_id
        _logger.info(f"Stopping WhatsApp session id={session_id}")

        try:
            self._make_api_request(f'sessions/{waha_session_id}', 'DELETE')
        except Exception as e:
            _logger.error(f"Failed to stop session id={session_id}: {e}")
            raise UserError(_("Failed to stop session: %s") % str(e))

        self.status = 'stopped'
        self.qr_code = False
        self.qr_code_image = False
        self.error_message = False

        _logger.info(f"Session id={session_id} stopped successfully")
        self.message_post(
            body=_("WhatsApp session stopped successfully."),
            subject=_("Session Stopped")
        )

    def action_get_qr_code(self):
        """Get QR code for session"""
        self.ensure_one()

        _logger.info(f"Fetching QR code for session: {self.name}")

        try:
            # استخدام الـ URL الصحيح مع session parameter
            params = {'session': self.session_id}
            screenshot = self._make_api_request('screenshot', 'GET', params=params)

            if isinstance(screenshot, bytes):
                self.qr_code_image = base64.b64encode(screenshot)
                _logger.info(f"QR Code retrieved successfully for session {self.name}")

                self.message_post(
                    body=_("QR Code retrieved successfully. Please scan to authenticate."),
                    subject=_("QR Code Retrieved")
                )
            else:
                _logger.debug(f"No screenshot data received for session {self.name}")

                self.message_post(
                    body=_("No QR code data received. Session might already be authenticated."),
                    subject=_("QR Code Request")
                )

            # Get session status
            self.action_refresh_status()

        except Exception as e:
            self.error_message = str(e)

            _logger.error(f"Failed to get QR code for {self.name}: {e}")

            self.message_post(
                body=_("Failed to get QR code: %s") % str(e),
                subject=_("QR Code Error")
            )

            raise UserError(_("Failed to get QR code: %s") % str(e))

    def action_refresh_status(self):
        """Refresh session status"""
        self.ensure_one()

        session_id = self.id
        waha_session_id = self.session_id
        _logger.debug(f"Refreshing status for session id={session_id}")

        try:
            status_data = self._make_api_request(f'sessions/{waha_session_id}')

            if status_data:
                old_status = self.status
                new_status = status_data.get('status', 'stopped').lower()

                me_data = status_data.get('me') or {}
                new_phone = me_data.get('id', '').replace('@c.us', '') if me_data else ''
                old_phone = self.phone_number

                self.status = new_status
                if me_data:
                    self.phone_number = new_phone
                    self.profile_name = me_data.get('pushName', '')
                self.last_sync = fields.Datetime.now()
                self.error_message = False

                # Log profile update
                if me_data and old_phone != new_phone and new_phone:
                    _logger.info(f"Session id={session_id} authenticated with phone: {new_phone}")
                    self.message_post(
                        body=_("Session authenticated successfully!<br/>Phone: %s<br/>Profile: %s") % (
                            new_phone, me_data.get('pushName', '') or 'N/A'
                        ),
                        subject=_("Session Authenticated")
                    )

                # Log status changes
                if old_status != new_status:
                    _logger.info(f"Session id={session_id} status changed: {old_status} → {new_status}")
                    if new_status == 'working':
                        self.message_post(
                            body=_("Session is now working and ready to send/receive messages."),
                            subject=_("Session Active")
                        )
                    elif new_status == 'scan_qr_code':
                        self.message_post(
                            body=_("Session is waiting for QR code scan."),
                            subject=_("Scan Required")
                        )
                    elif new_status == 'failed':
                        self.message_post(
                            body=_("Session has failed. Please check logs and restart."),
                            subject=_("Session Failed")
                        )

        except Exception as e:
            self.error_message = str(e)
            _logger.error(f"Failed to refresh status for session id={session_id}: {e}")

    def resolve_lid_to_chat_id(self, chat_id):
        """If chat_id ends with @lid, resolve it to a real @c.us chat ID via WAHA LID API.
        Returns the resolved chat_id, or the original if resolution fails.
        """
        if not chat_id or '@lid' not in chat_id:
            return chat_id
        try:
            lid_encoded = chat_id.replace('@', '%40')
            data = self._make_api_request(f'{self.session_id}/lids/{lid_encoded}', 'GET')
            pn = data.get('pn', '') if isinstance(data, dict) else ''
            if pn:
                resolved = pn if '@' in pn else f"{pn}@c.us"
                _logger.info(f"Resolved LID {chat_id} -> {resolved}")
                return resolved
        except Exception as e:
            _logger.debug(f"Could not resolve LID {chat_id}: {e}")
        return chat_id

    def fetch_chat_messages_from_api(self, chat_id, limit=50, offset=0):
        """Fetch messages directly from WAHA API for a given chat.
        Raises on failure so the caller can decide to fall back.
        WAHA returns messages newest-first.
        """
        self.ensure_one()
        resolved_id = self.resolve_lid_to_chat_id(chat_id)
        params = {'limit': limit, 'offset': offset}
        result = self._make_api_request(
            f'{self.session_id}/chats/{resolved_id}/messages',
            'GET',
            params=params,
        )
        return result if isinstance(result, list) else []

    def send_message(self, chat_id, text, file_data=None, file_name=None, file_type=None):
        """Send message through WhatsApp"""
        self.ensure_one()

        if self.status != 'working':
            _logger.warning(f"Attempted to send message on inactive session {self.name} (status: {self.status})")
            raise UserError(_("Session is not active. Current status: %s") % self.status)

        _logger.info(f"Sending message from session {self.name} to {chat_id}")

        try:
            if file_data:
                # Send file message
                endpoint = 'sendImage' if file_type and file_type.startswith('image/') else 'sendFile'

                data = {
                    'chatId': chat_id,
                    'session': self.session_id,
                    'file': {
                        'mimetype': file_type or 'application/octet-stream',
                        'filename': file_name or 'file',
                        'data': file_data
                    }
                }

                if text:
                    data['caption'] = text

                _logger.info(f"Sending file message: {file_name} to {chat_id}")

            else:
                # Send text message
                endpoint = 'sendText'
                data = {
                    'chatId': chat_id,
                    'text': text,
                    'session': self.session_id
                }

            result = self._make_api_request(endpoint, 'POST', data)

            _logger.info(f"Message sent successfully from {self.name} to {chat_id}")

            return result

        except Exception as e:
            _logger.error(f"Failed to send message from session {self.name}: {e}")
            raise UserError(_("Failed to send message: %s") % str(e))

    def send_voice_message(self, chat_id, voice_data, voice_filename='voice.ogg'):
        """Send voice message"""
        self.ensure_one()

        _logger.info(f"Sending voice message from session {self.name} to {chat_id}")

        try:
            data = {
                'chatId': chat_id,
                'session': self.session_id,
                'file': {
                    'mimetype': 'audio/ogg',
                    'filename': voice_filename,
                    'data': voice_data
                }
            }

            result = self._make_api_request('sendVoice', 'POST', data)

            _logger.info(f"Voice message sent successfully from {self.name} to {chat_id}")

            return result

        except Exception as e:
            _logger.error(f"Failed to send voice message from session {self.name}: {e}")
            raise UserError(_("Failed to send voice message: %s") % str(e))

    def send_seen(self, chat_id, message_ids, participant=None):
        """Mark messages as seen via WAHA /api/sendSeen"""
        self.ensure_one()
        data = {
            'chatId': chat_id,
            'session': self.session_id,
            'messageIds': message_ids,
            'participant': participant,
        }
        return self._make_api_request('sendSeen', 'POST', data)

    def start_typing(self, chat_id):
        """Send typing indicator via WAHA /api/startTyping"""
        self.ensure_one()
        data = {'chatId': chat_id, 'session': self.session_id}
        return self._make_api_request('startTyping', 'POST', data)

    def stop_typing(self, chat_id):
        """Send stop-typing indicator via WAHA /api/stopTyping"""
        self.ensure_one()
        data = {'chatId': chat_id, 'session': self.session_id}
        return self._make_api_request('stopTyping', 'POST', data)

    def action_view_messages(self):
        """View all messages for this session"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'WhatsApp Messages',
            'res_model': 'sadeem.waha.whatsapp.message',
            'view_mode': 'list,form',
            'domain': [('session_id', '=', self.id)],
            'context': {'default_session_id': self.id}
        }

    def action_view_sent_messages(self):
        """View outgoing messages for this session"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sent Messages',
            'res_model': 'sadeem.waha.whatsapp.message',
            'view_mode': 'list,form',
            'domain': [('session_id', '=', self.id), ('direction', '=', 'outgoing')],
            'context': {'default_session_id': self.id, 'default_direction': 'outgoing'}
        }

    def action_view_received_messages(self):
        """View incoming messages for this session"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Received Messages',
            'res_model': 'sadeem.waha.whatsapp.message',
            'view_mode': 'list,form',
            'domain': [('session_id', '=', self.id), ('direction', '=', 'incoming')],
            'context': {'default_session_id': self.id, 'default_direction': 'incoming'}
        }

    def action_update_webhook(self):
        """Clear all existing webhooks then register the new one"""
        self.ensure_one()

        session_id = self.id
        _logger.info(f"Updating webhook configuration for session id={session_id}")

        if not self.webhook_url:
            raise UserError(_("No webhook URL configured."))

        # Flush pending ORM writes before API calls so there are no dirty
        # records that could cause a SerializationFailure later.
        self.env.cr.flush()

        webhook_url = self.webhook_url
        webhook_events = self.webhook_events
        waha_session_id = self.session_id
        events = [e.strip() for e in webhook_events.split(',')] if webhook_events else ['message']

        # Fetch existing session config to preserve current webhooks
        existing_webhooks = []
        try:
            session_info = self._make_api_request(f'sessions/{waha_session_id}', 'GET')
            existing_webhooks = (session_info or {}).get('config', {}).get('webhooks', [])
        except Exception as e:
            _logger.debug(f"Could not fetch existing webhooks for session id={session_id}: {e}")

        existing_webhooks.append({'url': webhook_url, 'events': events})

        self._make_api_request(f'sessions/{waha_session_id}', 'PUT', {
            'name': waha_session_id,
            'config': {
                'noweb': {'store': {'enabled': True, 'full_sync': True}},
                'webhooks': existing_webhooks,
            }
        })

        _logger.info(f"Webhook updated successfully for session id={session_id}: {webhook_url}")

        # Avoid writing to the session record here — concurrent webhook
        # transactions update the same row and cause SerializationFailure.
        # The success notification below is sufficient feedback to the user.

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _('Webhook configuration updated successfully!'),
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model
    def _get_default_webhook_url(self):
        """Get default webhook URL based on Odoo base URL"""
        try:
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            if base_url:
                base_url = base_url.rstrip('/')
                return f"{base_url}/waha/whatsapp/webhook"
            else:
                return "http://localhost:8069/waha/whatsapp/webhook"
        except:
            return "http://localhost:8069/waha/whatsapp/webhook"

    def action_set_default_webhook(self):
        """Set default webhook URL automatically"""
        self.ensure_one()

        default_webhook = self._get_default_webhook_url()
        self.webhook_url = default_webhook

        _logger.info(f"Default webhook URL set for session {self.name}: {default_webhook}")

        self.message_post(
            body=_("Default webhook URL has been set: %s") % default_webhook,
            subject=_("Webhook Configured")
        )

        # حفظ التغييرات صراحة
        self.env.cr.commit()

        return {
            'type': 'ir.actions.act_window',
            'name': _('WhatsApp Session'),
            'res_model': 'sadeem.waha.session',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'show_notification': True,
                'notification_message': _('Default webhook URL set: %s') % default_webhook
            }
        }

    def action_auto_configure_webhook(self):
        """Auto configure webhook with default settings"""
        self.ensure_one()

        session_id = self.id
        _logger.info(f"Auto-configuring webhook for session id={session_id}")

        self.webhook_url = self._get_default_webhook_url()

        if not self.webhook_events:
            self.webhook_events = 'message.any,message.ack,session.status'

        # Flush pending ORM writes before calling action_update_webhook so
        # there are no dirty records when it flushes internally.
        self.env.cr.flush()

        if self.status in ['working', 'scan_qr_code']:
            try:
                self.action_update_webhook()
                message = _('Webhook auto-configured and updated successfully!')
            except Exception as e:
                _logger.warning(f"Auto-configure webhook update failed for session id={session_id}: {e}")
                message = _('Webhook URL set. Please update webhook manually.')
        else:
            message = _('Webhook URL configured. Will be applied when session starts.')
            self.message_post(
                body=_("Webhook auto-configured.<br/>URL: %s<br/>Events: %s") % (
                    self.webhook_url, self.webhook_events
                ),
                subject=_("Auto Configuration")
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model
    def default_get(self, fields_list):
        """Set default values including webhook URL"""
        res = super().default_get(fields_list)

        if 'webhook_url' in fields_list and not res.get('webhook_url'):
            res['webhook_url'] = self._get_default_webhook_url()

        if 'webhook_events' in fields_list and not res.get('webhook_events'):
            res['webhook_events'] = 'message.any,message.ack,session.status'

        return res

    @api.model
    def cron_refresh_all_sessions(self):
        """Cron job to refresh all active sessions"""
        _logger.info("Starting cron job: Refresh all active WhatsApp sessions")

        active_sessions = self.search([('active', '=', True)])

        _logger.info(f"Found {len(active_sessions)} active sessions to refresh")

        success_count = 0
        error_count = 0

        for session in active_sessions:
            try:
                session.action_refresh_status()
                success_count += 1
            except Exception as e:
                error_count += 1
                _logger.error(f"Failed to refresh session {session.name}: {e}")

        _logger.info(f"Cron job completed: {success_count} successful, {error_count} failed")