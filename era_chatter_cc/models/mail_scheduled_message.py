from odoo import models


class MailScheduledMessage(models.Model):
    _inherit = 'mail.scheduled.message'

    def _notification_parameters_whitelist(self):
        """ Let the Cc list ride along in 'notification_parameters' so that a
        message scheduled from the full composer is still posted with its Cc.
        """
        return super()._notification_parameters_whitelist() | {'partner_cc_ids'}
