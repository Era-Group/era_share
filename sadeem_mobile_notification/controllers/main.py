import logging
import uuid

from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class MobileNotificationController(http.Controller):

    @http.route('/sadeem/mobile/register_device', type='jsonrpc', auth='user', csrf=False, methods=['POST'])
    def register_device(self, device_name, platform, app_version=None, os_version=None,
                        device_model=None, topic=None, fcm_token=None, **kwargs):
        """Register or update a mobile device for push notifications.

        Args:
            device_name (str): Human-readable device name.
            platform (str): One of 'android', 'ios', 'web'.
            app_version (str, optional): Application version string.
            os_version (str, optional): Operating system version.
            device_model (str, optional): Device model name.
            topic (str, optional): Existing ntfy topic or FCM token.
            fcm_token (str, optional): FCM device token.

        Returns:
            dict: Registration result including provider info and topic.
        """
        user = request.env.user
        company = user.company_id
        config = company.default_notification_config_id

        if not config:
            return {'success': False, 'error': _('No default notification config set for company.')}

        # For FCM, use fcm_token as the device identity; for ntfy, generate a topic
        if not topic:
            if config.provider == 'fcm' and fcm_token:
                topic = fcm_token
            else:
                random_part = uuid.uuid4().hex[:8]
                topic = 'user_%s_%s_%s' % (user.id, uuid.uuid4().hex[:12], random_part)

        Device = request.env['notification.device'].sudo()

        # For FCM, also match by fcm_token to avoid duplicates when token rotates
        if config.provider == 'fcm' and fcm_token:
            existing = Device.search([
                ('fcm_token', '=', fcm_token),
                ('user_id', '=', user.id),
                ('config_id', '=', config.id),
            ], limit=1)
        else:
            existing = Device.search([
                ('topic', '=', topic),
                ('config_id', '=', config.id),
            ], limit=1)

        vals = {
            'name': device_name,
            'user_id': user.id,
            'config_id': config.id,
            'platform': platform if platform in ('android', 'ios', 'web') else False,
            'app_version': app_version,
            'os_version': os_version,
            'device_model': device_model,
            'topic': topic,
            'fcm_token': fcm_token or '',
            'last_seen': request.env.cr.now(),
        }

        if existing:
            existing.write(vals)
            _logger.info('Device updated: %s for user %s', topic, user.id)
        else:
            vals['registration_date'] = request.env.cr.now()
            Device.create(vals)
            _logger.info('Device registered: %s for user %s', topic, user.id)

        result = {
            'success': True,
            'provider': config.provider,
            'topic': topic,
        }

        if config.provider == 'ntfy':
            result['server_url'] = config.ntfy_server_url or ''
        elif config.provider == 'fcm':
            result['fcm_project_id'] = config.fcm_project_id or ''
            result['fcm_api_key'] = config.sudo().fcm_api_key or ''
            result['fcm_app_id'] = config.fcm_app_id or ''
            result['fcm_messaging_sender_id'] = config.fcm_messaging_sender_id or ''

        return result

    @http.route('/sadeem/mobile/unregister_device', type='jsonrpc', auth='user', csrf=False, methods=['POST'])
    def unregister_device(self, topic, **kwargs):
        """Unregister a mobile device.

        Args:
            topic (str): The device topic/token to unregister.

        Returns:
            dict: {'success': bool, 'error': str or None}
        """
        user = request.env.user
        device = request.env['notification.device'].sudo().search([
            ('topic', '=', topic),
            ('user_id', '=', user.id),
        ], limit=1)

        if not device:
            return {'success': False, 'error': _('Device not found.')}

        device.write({'active': False})
        _logger.info('Device unregistered: %s for user %s', topic, user.id)
        return {'success': True, 'error': None}

    @http.route('/sadeem/mobile/test_notification', type='jsonrpc', auth='user', csrf=False, methods=['POST'])
    def test_notification(self, title, message, **kwargs):
        """Send a test notification to all devices of the current user.

        Args:
            title (str): Notification title.
            message (str): Notification body.

        Returns:
            dict: Summary of results.
        """
        user = request.env.user
        devices = request.env['notification.device'].sudo().search([
            ('user_id', '=', user.id),
            ('active', '=', True),
        ])

        if not devices:
            return {'success': False, 'error': _('No registered devices found.')}

        results = []
        for device in devices:
            res = device.send_notification(title=title, message=message)
            results.append({
                'device': device.name,
                'success': res.get('success', False),
                'error': res.get('error'),
            })

        all_ok = all(r['success'] for r in results)
        return {
            'success': all_ok,
            'results': results,
        }
