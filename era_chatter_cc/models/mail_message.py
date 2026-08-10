from odoo import fields, models
from odoo.addons.mail.tools.discuss import Store


class MailMessage(models.Model):
    _inherit = 'mail.message'

    partner_cc_ids = fields.Many2many(
        'res.partner', 'mail_message_res_partner_cc_rel',
        'mail_message_id', 'partner_id',
        # not simply 'Cc': <mail.mail> delegates to <mail.message> and already
        # has an 'email_cc' field labelled that way
        string='Cc Recipients',
        context={'active_test': False},
        help="Recipients that were put in copy of this message. They are also "
             "part of 'Recipients' (partner_ids) and notified as such; this "
             "field only records which of them were on the Cc line.",
    )

    def _to_store_defaults(self, target: Store.Target):
        # sudo: res.partner - reading limited data of Cc recipients is
        # acceptable, same treatment as core gives 'partner_ids'.
        return super()._to_store_defaults(target) + [
            Store.Many('partner_cc_ids', ['email', 'name'], sort='id', sudo=True),
        ]
