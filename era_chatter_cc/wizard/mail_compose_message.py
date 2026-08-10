from odoo import Command, fields, models


class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    partner_cc_ids = fields.Many2many(
        'res.partner', 'mail_compose_message_res_partner_cc_rel',
        'wizard_id', 'partner_id',
        string='Cc',
    )

    def _prepare_mail_values(self, res_ids):
        """ Carry the Cc line over to the posted message.

        Only in 'comment' mode: Cc'ing the same addresses on every record of a
        mass mailing would be a footgun, and the Cc field is hidden in that
        mode anyway.
        """
        mail_values_all = super()._prepare_mail_values(res_ids)
        if self.composition_mode != 'comment' or not self.partner_cc_ids:
            return mail_values_all
        cc_ids = self.partner_cc_ids.ids
        for values in mail_values_all.values():
            values['partner_cc_ids'] = [Command.set(cc_ids)]
            # 'partner_ids' is a plain list of ids in comment mode
            values['partner_ids'] = list(set(values.get('partner_ids') or []) | set(cc_ids))
        return mail_values_all
