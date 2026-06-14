# -*- coding: utf-8 -*-

import re
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)

        for message in messages:
            try:
                if message.partner_ids and message.message_type != 'notification':
                    self._send_push_notification(message)
            except Exception as e:
                _logger.error('Failed to send push notification for mail.message: %s', e)

        return messages

    def _send_push_notification(self, message):
        """Send push notification to message recipients via registered devices."""
        partner_ids = message.partner_ids
        if not partner_ids:
            return

        users = self.env['res.users'].sudo().search([
            ('partner_id', 'in', partner_ids.ids)
        ])

        for user in users:
            # Skip the message author
            if user.partner_id == message.author_id:
                continue

            devices = self.env['notification.device'].sudo().search([
                ('user_id', '=', user.id),
                ('active', '=', True),
            ])
            if not devices:
                continue

            # Build notification content
            author_name = message.author_id.name or 'Odoo'
            title = author_name
            body = message.body or 'New message'

            # Strip HTML tags
            body = re.sub('<.*?>', '', body)
            body = body.strip()[:200]

            # Build click URL
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            click_url = ''
            if message.model and message.res_id:
                click_url = '%s/web#id=%s&model=%s' % (base_url, message.res_id, message.model)

            data = {
                'type': 'message',
                'message_id': message.id,
                'model': message.model or '',
                'res_id': message.res_id or 0,
            }

            for device in devices:
                try:
                    config = device.config_id
                    token = device.fcm_token or device.topic
                    if not token or not config:
                        continue
                    config.send_notification(
                        topic=token,
                        title=title,
                        message=body,
                        data=data,
                        user_id=user.id,
                        priority='default',
                        tags=['email', 'speech_balloon'],
                        click_url=click_url,
                    )
                except Exception as e:
                    _logger.error('Push notification failed for device %s: %s', device.id, e)
