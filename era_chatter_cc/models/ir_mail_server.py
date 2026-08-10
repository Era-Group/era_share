from odoo import api, models, tools


class IrMailServer(models.Model):
    _inherit = 'ir.mail_server'

    @api.model
    def _alter_message__(self, message, smtp_from, smtp_to_list):  # noqa: PLW3201
        """ Promote our private 'X-Msg-Cc-Add' header into a real 'Cc:' header.

        This runs after '_prepare_smtp_to_list' has frozen the envelope
        recipients, exactly like core's own 'X-Msg-To-Add' handling, so the
        addresses added here are visible to the reader and usable for
        "Reply All" without receiving a second copy of the message.
        """
        super()._alter_message__(message, smtp_from, smtp_to_list)

        cc_add = message['X-Msg-Cc-Add']
        del message['X-Msg-Cc-Add']
        if not cc_add:
            return

        existing_cc = message['Cc'] or ''
        taken = set(tools.mail.email_normalize_all(message['To'] or ''))
        taken.update(tools.mail.email_normalize_all(existing_cc))
        new_addresses = []
        for address in tools.mail.email_split_and_format(cc_add):
            normalized = tools.mail.email_normalize(address, strict=False)
            if normalized in taken:
                continue
            taken.add(normalized)
            new_addresses.append(address)
        if not new_addresses:
            return

        cc_value = ', '.join(filter(None, [existing_cc, ', '.join(new_addresses)]))
        if message['Cc']:
            message.replace_header('Cc', cc_value)
        else:
            message['Cc'] = cc_value
