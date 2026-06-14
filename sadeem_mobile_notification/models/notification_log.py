from odoo import fields, models


class NotificationLog(models.Model):
    _name = 'notification.log'
    _description = 'Notification Log'
    _order = 'create_date desc'

    config_id = fields.Many2one(
        'notification.config',
        string='Configuration',
        index=True,
        ondelete='set null',
    )
    user_id = fields.Many2one(
        'res.users',
        string='User',
        index=True,
    )
    topic = fields.Char(string='Topic / Token')
    title = fields.Char(string='Title')
    message = fields.Text(string='Message')
    status = fields.Selection(
        selection=[
            ('sent', 'Sent'),
            ('failed', 'Failed'),
        ],
        string='Status',
        required=True,
        index=True,
    )
    error_message = fields.Text(string='Error Message')
    sent_date = fields.Datetime(
        string='Sent Date',
        default=fields.Datetime.now,
        index=True,
    )
