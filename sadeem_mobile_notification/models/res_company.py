from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    default_notification_config_id = fields.Many2one(
        'notification.config',
        string='Default Notification Config',
        domain="[('company_id', '=', id), ('active', '=', True)]",
        help='The default notification configuration used for API device registration.',
    )
    notification_config_ids = fields.One2many(
        'notification.config',
        'company_id',
        string='Notification Configurations',
    )
    notification_config_count = fields.Integer(
        string='Config Count',
        compute='_compute_notification_config_count',
    )

    @api.depends('notification_config_ids')
    def _compute_notification_config_count(self):
        for company in self:
            company.notification_config_count = self.env['notification.config'].search_count(
                [('company_id', '=', company.id)]
            )
