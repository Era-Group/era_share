import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class NotificationDevice(models.Model):
    _name = 'notification.device'
    _description = 'Registered Notification Device'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'last_seen desc, id desc'

    _unique_topic_config = models.Constraint(
        'UNIQUE(topic, config_id)',
        'A device with this topic already exists for this configuration.',
    )

    name = fields.Char(
        string='Device Name',
        required=True,
        tracking=True,
        help='A human-readable name for the device.',
    )
    active = fields.Boolean(string='Active', default=True, tracking=True)
    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        tracking=True,
        index=True,
        help='The Odoo user who owns this device.',
    )
    config_id = fields.Many2one(
        'notification.config',
        string='Configuration',
        required=True,
        tracking=True,
        index=True,
        ondelete='cascade',
        help='The notification configuration used for this device.',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='user_id.company_id',
        store=True,
    )
    platform = fields.Selection(
        selection=[
            ('android', 'Android'),
            ('ios', 'iOS'),
            ('web', 'Web'),
        ],
        string='Platform',
        tracking=True,
        help='The device platform.',
    )
    app_version = fields.Char(string='App Version')
    os_version = fields.Char(string='OS Version')
    device_model = fields.Char(string='Device Model')
    topic = fields.Char(
        string='Topic / Token',
        index=True,
        help='ntfy topic name or FCM device token.',
    )
    fcm_token = fields.Char(
        string='FCM Token',
        groups='sadeem_mobile_notification.group_notification_admin',
        help='Firebase Cloud Messaging device token.',
    )
    last_seen = fields.Datetime(
        string='Last Seen',
        readonly=True,
        help='The last time this device contacted the server.',
    )
    registration_date = fields.Datetime(
        string='Registration Date',
        readonly=True,
        default=fields.Datetime.now,
    )
    notification_count = fields.Integer(
        string='Notifications Sent',
        compute='_compute_notification_count',
        help='Total number of notifications sent to this device.',
    )

    @api.depends('topic', 'config_id')
    def _compute_notification_count(self):
        """Count notifications sent to this device by matching topic and config."""
        for rec in self:
            if rec.topic and rec.config_id:
                rec.notification_count = self.env['notification.log'].search_count([
                    ('config_id', '=', rec.config_id.id),
                    ('topic', '=', rec.topic),
                ])
            else:
                rec.notification_count = 0

    def update_last_seen(self):
        """Update the last_seen timestamp to now."""
        self.write({'last_seen': fields.Datetime.now()})

    def send_notification(self, title, message, data=None):
        """Send a notification to this device.

        Args:
            title: Notification title.
            message: Notification body.
            data: Optional dict of extra payload data.

        Returns:
            dict: {'success': bool, 'error': str or None}
        """
        self.ensure_one()
        token = self.fcm_token or self.topic
        if not token:
            return {'success': False, 'error': _('No topic or token configured for this device.')}
        return self.config_id.send_notification(
            topic=token,
            title=title,
            message=message,
            data=data,
            user_id=self.user_id.id,
        )

    def action_send_test_notification(self):
        """Button action: send a test notification to this device."""
        self.ensure_one()
        result = self.send_notification(
            title=_('Test Notification'),
            message=_('Hello from Odoo! This is a test for device: %s') % self.name,
        )
        if result.get('success'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Test notification sent to %s!') % self.name,
                    'type': 'success',
                    'sticky': False,
                },
            }
        from odoo.exceptions import UserError
        raise UserError(_('Failed to send test notification: %s') % result.get('error', ''))
