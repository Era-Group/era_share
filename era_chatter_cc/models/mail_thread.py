import ast

from odoo import Command, models
from odoo.tools.mail import (
    email_normalize,
    email_split_and_format,
    formataddr,
)


def _extract_partner_ids(value):
    """ Turn a many2many value (plain ids or x2many commands) into a list of
    ids. Only the shapes that can reach 'message_post' are handled. """
    ids = []
    for item in value or []:
        if isinstance(item, int):
            ids.append(item)
        elif isinstance(item, (list, tuple)) and item:
            if item[0] in (Command.LINK, Command.UPDATE):
                ids.append(item[1])
            elif item[0] == Command.SET:
                ids.extend(item[2])
    return ids


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _get_message_create_valid_field_names(self):
        """ Allow 'partner_cc_ids' to be given to message_post(), otherwise
        '_message_create' refuses it as an unsupported value. """
        return super()._get_message_create_valid_field_names() | {'partner_cc_ids'}

    def message_post(self, *, partner_ids=None, **kwargs):
        """ Cc recipients are real recipients: make sure they are part of
        'partner_ids' so that the standard notification machinery gives them
        their own email, their own <mail.notification> and the usual
        failure handling. Which of them were on the Cc line stays recorded in
        'partner_cc_ids'.
        """
        cc_ids = _extract_partner_ids(kwargs.get('partner_cc_ids'))
        if cc_ids:
            kwargs['partner_cc_ids'] = [Command.set(cc_ids)]
            partner_ids = list(dict.fromkeys(list(partner_ids or []) + cc_ids))
        return super().message_post(partner_ids=partner_ids, **kwargs)

    def _notify_by_email_get_base_mail_values(self, message, recipients_data, additional_values=None):
        """ Add a private 'X-Msg-Cc-Add' header holding the Cc recipients of
        the message, and take those addresses out of core's 'X-Msg-To-Add'
        so that nobody shows up on both lines.

        'X-Msg-Cc-Add' is turned into a real, visible 'Cc:' header at sending
        time by our 'ir.mail_server._alter_message__' override. It is done
        there, and not by adding recipients to the mail itself, because that
        hook runs after the SMTP recipient list has been computed: the header
        is display-only and delivers no extra copy. The Cc people receive the
        message through their own notification email, as they are also part
        of 'partner_ids'.
        """
        values = super()._notify_by_email_get_base_mail_values(
            message, recipients_data, additional_values=additional_values,
        )
        # sudo: mail.message - reading recipients of a message being notified
        cc_partners = message.sudo().partner_cc_ids
        if not cc_partners:
            return values

        cc_formatted, cc_normalized = [], set()
        for partner in cc_partners:
            if not partner.email_normalized:
                continue
            cc_formatted.append(formataddr((partner.name or '', partner.email_normalized)))
            cc_normalized.add(partner.email_normalized)
        # more than threshold = considered as a public record -> do not leak,
        # same guard core applies to 'X-Msg-To-Add'
        if not cc_formatted or len(cc_formatted) >= self._CUSTOMER_HEADERS_LIMIT_COUNT:
            return values

        headers = ast.literal_eval(values['headers']) if values.get('headers') else {}
        headers['X-Msg-Cc-Add'] = ','.join(cc_formatted)
        if to_add := headers.get('X-Msg-To-Add'):
            remaining = [
                address for address in email_split_and_format(to_add)
                if email_normalize(address, strict=False) not in cc_normalized
            ]
            if remaining:
                headers['X-Msg-To-Add'] = ','.join(remaining)
            else:
                headers.pop('X-Msg-To-Add')
        values['headers'] = repr(headers)
        return values
