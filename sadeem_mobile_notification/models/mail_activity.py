# -*- coding: utf-8 -*-

import re
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    @api.model_create_multi
    def create(self, vals_list):
        activities = super().create(vals_list)

        for activity in activities:
            try:
                if activity.user_id:
                    self._send_push_notification(activity)
            except Exception as e:
                _logger.error('Failed to send push notification for mail.activity: %s', e)

        return activities

    def _send_push_notification(self, activity):
        """Send push notification when a new activity is assigned."""
        devices = self.env['notification.device'].sudo().search([
            ('user_id', '=', activity.user_id.id),
            ('active', '=', True),
        ])
        if not devices:
            return

        # Build notification content
        title = activity.activity_type_id.name or 'New Activity'
        body = activity.summary or activity.note or 'You have a new activity'

        # Strip HTML tags
        body = re.sub('<.*?>', '', body)
        body = body.strip()[:200]

        # Build click URL
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        click_url = '%s/web#id=%s&model=%s&view_type=form' % (
            base_url, activity.res_id, activity.res_model
        )

        data = {
            'type': 'activity',
            'activity_id': activity.id,
            'model': activity.res_model,
            'res_id': activity.res_id,
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
                    user_id=activity.user_id.id,
                    priority='high',
                    tags=['calendar', 'bell'],
                    click_url=click_url,
                )
            except Exception as e:
                _logger.error('Push notification failed for device %s: %s', device.id, e)
