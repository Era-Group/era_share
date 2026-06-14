import json
import logging
import requests
from datetime import date

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class NotificationConfig(models.Model):
    _name = 'notification.config'
    _description = 'Notification Configuration'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, id'

    name = fields.Char(
        string='Name',
        required=True,
        tracking=True,
        help='A descriptive name for this notification configuration.',
    )
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True, tracking=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    provider = fields.Selection(
        selection=[('ntfy', 'ntfy'), ('fcm', 'FCM')],
        string='Provider',
        required=True,
        default='ntfy',
        tracking=True,
        help='The push notification provider to use.',
    )

    # ── ntfy fields ──────────────────────────────────────────────
    ntfy_server_url = fields.Char(
        string='ntfy Server URL',
        help='URL of the ntfy server, e.g. https://ntfy.sh',
    )
    ntfy_auth_method = fields.Selection(
        selection=[
            ('none', 'No Authentication'),
            ('token', 'Access Token'),
            ('basic', 'Username & Password'),
        ],
        string='Authentication Method',
        default='none',
    )
    ntfy_auth_token = fields.Char(
        string='ntfy Access Token',
        groups='sadeem_mobile_notification.group_notification_admin',
        help='ntfy access token (format: tk_xxxxx). Used as HTTP Basic auth with empty username.',
    )
    ntfy_username = fields.Char(
        string='ntfy Username',
        groups='sadeem_mobile_notification.group_notification_admin',
        help='Username for password authentication.',
    )
    ntfy_password = fields.Char(
        string='ntfy Password',
        groups='sadeem_mobile_notification.group_notification_admin',
        help='Password for username/password authentication.',
    )

    # ── FCM fields ───────────────────────────────────────────────
    fcm_server_key = fields.Char(
        string='FCM Server Key (Legacy)',
        groups='sadeem_mobile_notification.group_notification_admin',
        help='Legacy FCM server key for the Cloud Messaging API.',
    )
    fcm_project_id = fields.Char(
        string='FCM Project ID',
        help='Firebase project ID.',
    )
    fcm_service_account_json = fields.Text(
        string='Service Account JSON',
        groups='sadeem_mobile_notification.group_notification_admin',
        help='Contents of the Firebase service account JSON file.',
    )
    fcm_api_key = fields.Char(
        string='FCM API Key',
        groups='sadeem_mobile_notification.group_notification_admin',
        help='Firebase Web API key (from google-services.json → api_key).',
    )
    fcm_app_id = fields.Char(
        string='FCM App ID',
        help='Firebase App ID (from google-services.json → mobilesdk_app_id).',
    )
    fcm_messaging_sender_id = fields.Char(
        string='FCM Messaging Sender ID',
        help='Firebase Messaging Sender ID (from google-services.json → project_number).',
    )

    # ── Rate limiting ────────────────────────────────────────────
    monthly_limit = fields.Integer(
        string='Monthly Limit',
        default=10000,
        help='Maximum number of notifications per month. 0 = unlimited.',
    )
    current_month_count = fields.Integer(
        string='Current Month Count',
        readonly=True,
        default=0,
    )
    last_reset_date = fields.Date(
        string='Last Reset Date',
        readonly=True,
    )

    # ── Statistics (computed) ────────────────────────────────────
    device_count = fields.Integer(
        string='Device Count',
        compute='_compute_statistics',
    )
    total_sent = fields.Integer(
        string='Total Sent',
        compute='_compute_statistics',
    )
    total_failed = fields.Integer(
        string='Total Failed',
        compute='_compute_statistics',
    )
    success_rate = fields.Float(
        string='Success Rate (%)',
        compute='_compute_statistics',
    )

    # ── Relations ────────────────────────────────────────────────
    device_ids = fields.One2many(
        'notification.device',
        'config_id',
        string='Devices',
    )
    log_ids = fields.One2many(
        'notification.log',
        'config_id',
        string='Logs',
    )

    # ── Validation ────────────────────────────────────────────

    @api.constrains('ntfy_server_url')
    def _check_ntfy_url(self):
        for record in self:
            if record.ntfy_server_url:
                url = record.ntfy_server_url.strip()
                if not url.startswith(('http://', 'https://')):
                    raise ValidationError(_('ntfy Server URL must start with http:// or https://'))

    @api.constrains('ntfy_auth_method', 'ntfy_auth_token', 'ntfy_username', 'ntfy_password')
    def _check_ntfy_auth(self):
        for record in self:
            if record.provider != 'ntfy':
                continue
            if record.ntfy_auth_method == 'token' and record.ntfy_auth_token:
                if not record.ntfy_auth_token.startswith('tk_'):
                    raise ValidationError(_(
                        'Invalid ntfy token format. Token should start with "tk_". '
                        'Example: tk_abc123def456'
                    ))
            if record.ntfy_auth_method == 'basic':
                if not record.ntfy_username or not record.ntfy_password:
                    raise ValidationError(_(
                        'Username and Password are required when using basic authentication.'
                    ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('ntfy_server_url'):
                vals['ntfy_server_url'] = vals['ntfy_server_url'].rstrip('/')
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('ntfy_server_url'):
            vals['ntfy_server_url'] = vals['ntfy_server_url'].rstrip('/')
        return super().write(vals)

    @api.depends('device_ids', 'log_ids', 'log_ids.status')
    def _compute_statistics(self):
        """Compute device count, sent/failed totals, and success rate."""
        for rec in self:
            rec.device_count = self.env['notification.device'].search_count(
                [('config_id', '=', rec.id)]
            )
            logs = self.env['notification.log'].search([('config_id', '=', rec.id)])
            rec.total_sent = len(logs.filtered(lambda l: l.status == 'sent'))
            rec.total_failed = len(logs.filtered(lambda l: l.status == 'failed'))
            total = rec.total_sent + rec.total_failed
            rec.success_rate = (rec.total_sent / total * 100) if total else 0.0

    # ── Public API ───────────────────────────────────────────────

    def send_notification(self, topic, title, message, data=None, user_id=None,
                          priority=None, tags=None, click_url=None):
        """Send a push notification through the configured provider.

        Args:
            topic: ntfy topic or FCM token/topic.
            title: Notification title.
            message: Notification body.
            data: Optional dict of extra data.
            user_id: Optional res.users id for logging.
            priority: ntfy priority (min, low, default, high, urgent).
            tags: list of ntfy emoji tags (e.g. ['bell', 'email']).
            click_url: URL to open when notification is clicked (ntfy).

        Returns:
            dict: {'success': bool, 'error': str or None}
        """
        self.ensure_one()
        self._check_and_reset_monthly_limit()

        if self.monthly_limit and self.current_month_count >= self.monthly_limit:
            error = _('Monthly notification limit (%s) reached.') % self.monthly_limit
            self._create_log(topic, title, message, 'failed', error, user_id)
            return {'success': False, 'error': error}

        if self.provider == 'ntfy':
            result = self._send_ntfy(topic, title, message, data,
                                     priority=priority, tags=tags, click_url=click_url)
        elif self.provider == 'fcm':
            result = self._send_fcm(topic, title, message, data)
        else:
            result = {'success': False, 'error': _('Unknown provider: %s') % self.provider}

        status = 'sent' if result.get('success') else 'failed'
        self._create_log(topic, title, message, status, result.get('error'), user_id)

        if result.get('success'):
            self.sudo().write({
                'current_month_count': self.current_month_count + 1,
            })

        return result

    # ── Private send helpers ─────────────────────────────────────

    def _get_ntfy_auth(self):
        """Get authentication tuple for ntfy requests.

        ntfy uses HTTP Basic auth for both password and token modes.
        For token auth: empty username + token as password -> auth=('', 'tk_xxx')
        For basic auth: username + password -> auth=(user, pass)

        Returns:
            tuple or None: (username, password) for requests auth parameter.
        """
        self.ensure_one()
        if self.ntfy_auth_method == 'token' and self.ntfy_auth_token:
            return ('', self.ntfy_auth_token)
        elif self.ntfy_auth_method == 'basic' and self.ntfy_username and self.ntfy_password:
            return (self.ntfy_username, self.ntfy_password)
        return None

    def _send_ntfy(self, topic, title, message, data=None,
                   priority=None, tags=None, click_url=None):
        """Send a notification via ntfy.

        Args:
            topic: ntfy topic name.
            title: Notification title.
            message: Notification body text.
            data: Optional dict to send as X-Data JSON header.
            priority: ntfy priority (min, low, default, high, urgent).
            tags: list of ntfy emoji tag names (e.g. ['bell', 'email']).
            click_url: URL to open when notification is clicked.

        Returns:
            dict: {'success': bool, 'error': str or None}
        """
        self.ensure_one()
        url = (self.ntfy_server_url or '').rstrip('/')
        if not url:
            return {'success': False, 'error': _('ntfy server URL is not configured.')}

        headers = {
            'Content-Type': 'text/plain; charset=utf-8',
            'Title': (title or '')[:100],
        }
        if priority:
            headers['Priority'] = priority
        if tags:
            headers['Tags'] = ','.join(tags)
        if click_url:
            headers['Click'] = click_url
        if data:
            headers['X-Data'] = json.dumps(data)

        auth = self._get_ntfy_auth()

        try:
            resp = requests.post(
                '%s/%s' % (url, topic),
                data=(message or '').encode('utf-8'),
                headers=headers,
                auth=auth,
                timeout=10,
            )
            if resp.ok:
                _logger.info('ntfy notification sent to topic=%s', topic)
                return {'success': True, 'error': None}
            if resp.status_code == 401:
                error = _('ntfy authentication failed. Please check your credentials (token or username/password).')
            elif resp.status_code == 403:
                error = _('ntfy access forbidden. User may not have publish permission on topic "%s".') % topic
            else:
                error = 'ntfy HTTP %s: %s' % (resp.status_code, resp.text[:200])
            _logger.error(error)
            return {'success': False, 'error': error}
        except requests.exceptions.ConnectionError:
            error = _('Cannot connect to ntfy server at %s. Please check URL and network.') % url
            _logger.error(error)
            return {'success': False, 'error': error}
        except requests.exceptions.Timeout:
            error = _('Request timeout. ntfy server at %s is not responding.') % url
            _logger.error(error)
            return {'success': False, 'error': error}
        except Exception as e:
            _logger.error('ntfy send error: %s', e)
            return {'success': False, 'error': str(e)}

    def _send_fcm(self, token, title, message, data=None):
        """Send a notification via Firebase Cloud Messaging.

        Supports the legacy HTTP API. For FCM v1, the service account
        JSON must be configured and google-auth installed.

        Returns:
            dict: {'success': bool, 'error': str or None}
        """
        self.ensure_one()

        # Try v1 API first if service account is configured
        if self.fcm_service_account_json and self.fcm_project_id:
            return self._send_fcm_v1(token, title, message, data)

        # Fall back to legacy API
        if not self.fcm_server_key:
            return {'success': False, 'error': _('FCM server key is not configured.')}

        headers = {
            'Authorization': 'key=%s' % self.fcm_server_key,
            'Content-Type': 'application/json',
        }
        payload = {
            'to': token,
            'notification': {
                'title': title or '',
                'body': message or '',
            },
        }
        if data:
            payload['data'] = data

        try:
            resp = requests.post(
                'https://fcm.googleapis.com/fcm/send',
                json=payload,
                headers=headers,
                timeout=10,
            )
            if resp.ok:
                result = resp.json()
                if result.get('success', 0) >= 1:
                    _logger.info('FCM notification sent to token=%s...', token[:20] if token else '')
                    return {'success': True, 'error': None}
                error = 'FCM failure: %s' % json.dumps(result.get('results', []))
                _logger.error(error)
                return {'success': False, 'error': error}
            error = 'FCM HTTP %s: %s' % (resp.status_code, resp.text[:200])
            _logger.error(error)
            return {'success': False, 'error': error}
        except Exception as e:
            _logger.error('FCM send error: %s', e)
            return {'success': False, 'error': str(e)}

    def _send_fcm_v1(self, token, title, message, data=None):
        """Send notification via FCM v1 HTTP API using service account credentials.

        Returns:
            dict: {'success': bool, 'error': str or None}
        """
        self.ensure_one()
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
        except ImportError:
            return {
                'success': False,
                'error': _('google-auth library is not installed. '
                           'Install it with: pip install google-auth'),
            }

        try:
            sa_info = json.loads(self.fcm_service_account_json)
            credentials = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=['https://www.googleapis.com/auth/firebase.messaging'],
            )
            credentials.refresh(Request())

            headers = {
                'Authorization': 'Bearer %s' % credentials.token,
                'Content-Type': 'application/json',
            }
            payload = {
                'message': {
                    'token': token,
                    'notification': {
                        'title': title or '',
                        'body': message or '',
                    },
                }
            }
            if data:
                payload['message']['data'] = {k: str(v) for k, v in data.items()}

            url = 'https://fcm.googleapis.com/v1/projects/%s/messages:send' % self.fcm_project_id
            resp = requests.post(url, json=payload, headers=headers, timeout=10)

            if resp.ok:
                _logger.info('FCM v1 notification sent to token=%s...', token[:20] if token else '')
                return {'success': True, 'error': None}
            error = 'FCM v1 HTTP %s: %s' % (resp.status_code, resp.text[:200])
            _logger.error(error)
            return {'success': False, 'error': error}
        except Exception as e:
            _logger.error('FCM v1 send error: %s', e)
            return {'success': False, 'error': str(e)}

    # ── Rate limit helpers ───────────────────────────────────────

    def _check_and_reset_monthly_limit(self):
        """Reset the monthly counter if the month has changed."""
        self.ensure_one()
        today = date.today()
        if not self.last_reset_date or self.last_reset_date.month != today.month or self.last_reset_date.year != today.year:
            self.sudo().write({
                'current_month_count': 0,
                'last_reset_date': today,
            })

    # ── Log helper ───────────────────────────────────────────────

    def _create_log(self, topic, title, message, status, error=None, user_id=None):
        """Create a notification log entry."""
        self.env['notification.log'].sudo().create({
            'config_id': self.id,
            'user_id': user_id or self.env.uid,
            'topic': topic,
            'title': title,
            'message': message,
            'status': status,
            'error_message': error or '',
            'sent_date': fields.Datetime.now(),
        })

    # ── UI Actions ───────────────────────────────────────────────

    def action_test_connection(self):
        """Test connectivity to the notification server (ntfy health check or FCM endpoint)."""
        self.ensure_one()
        if self.provider == 'ntfy':
            url = (self.ntfy_server_url or '').rstrip('/')
            if not url:
                raise UserError(_('ntfy server URL is not configured.'))
            try:
                auth = self._get_ntfy_auth()
                resp = requests.get('%s/v1/health' % url, auth=auth, timeout=10)
                if resp.status_code == 200:
                    auth_status = _('with authentication') if auth else _('without authentication')
                    self.message_post(body=_('Connection test successful %s! Server is healthy.') % auth_status)
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _('ntfy server is reachable and healthy (%s)') % auth_status,
                            'type': 'success',
                            'sticky': False,
                        },
                    }
                elif resp.status_code == 401:
                    raise UserError(_('Authentication failed. Please check your credentials.'))
                elif resp.status_code == 403:
                    raise UserError(_('Access forbidden. User may not have required permissions.'))
                else:
                    raise UserError(_('Server returned status %s. Expected 200.') % resp.status_code)
            except requests.exceptions.ConnectionError:
                raise UserError(_('Cannot connect to ntfy server at %s. Please check the URL.') % url)
            except requests.exceptions.Timeout:
                raise UserError(_('Connection timeout. Server at %s is not responding.') % url)
            except UserError:
                raise
            except Exception as e:
                raise UserError(_('Connection test failed: %s') % str(e))
        else:
            # FCM - just validate config is present
            if self.fcm_service_account_json and self.fcm_project_id:
                try:
                    json.loads(self.fcm_service_account_json)
                except (json.JSONDecodeError, ValueError) as e:
                    raise UserError(_('Invalid Service Account JSON: %s') % str(e))
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('OK'),
                        'message': _('FCM configuration looks valid. Use "Send Test" to verify delivery.'),
                        'type': 'success',
                        'sticky': False,
                    },
                }
            elif self.fcm_server_key:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('OK'),
                        'message': _('FCM Legacy key configured. Use "Send Test" to verify delivery.'),
                        'type': 'success',
                        'sticky': False,
                    },
                }
            raise UserError(_('No FCM credentials configured.'))

    def action_test_notification(self):
        """Send a test notification to verify the configuration.

        For ntfy: sends to a test topic (any string is valid).
        For FCM: sends to registered devices (FCM requires real tokens).
        """
        self.ensure_one()
        title = _('Test Notification')
        message = _('This is a test notification from Odoo - %s') % self.name

        if self.provider == 'ntfy':
            # ntfy accepts any topic string
            topic = 'test_%s' % self.id
            result = self.send_notification(topic=topic, title=title, message=message)
        else:
            # FCM requires a real device token - send to registered devices
            devices = self.env['notification.device'].search([
                ('config_id', '=', self.id),
                ('active', '=', True),
            ])
            if not devices:
                raise UserError(_(
                    'No registered devices found for this FCM configuration. '
                    'Register a device first using the API endpoint /sadeem/mobile/register_device.'
                ))
            result = {'success': False, 'error': ''}
            for device in devices:
                token = device.fcm_token or device.topic
                if token:
                    result = self.send_notification(
                        topic=token, title=title, message=message,
                        user_id=device.user_id.id,
                    )
                    if result.get('success'):
                        break

        if result.get('success'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Test notification sent successfully!'),
                    'type': 'success',
                    'sticky': False,
                },
            }
        raise UserError(_('Test notification failed: %s') % result.get('error', ''))

    def action_reset_counter(self):
        """Manually reset the monthly notification counter."""
        self.ensure_one()
        self.sudo().write({
            'current_month_count': 0,
            'last_reset_date': date.today(),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Counter Reset'),
                'message': _('Monthly counter has been reset to 0.'),
                'type': 'info',
                'sticky': False,
            },
        }
