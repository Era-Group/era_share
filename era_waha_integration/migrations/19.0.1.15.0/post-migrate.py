# Remove phantom WAHA messages: entries with no body and no attachment, which Discuss
# renders as "This message has been removed". They came from WhatsApp protocol/system
# notifications (E2E session setup, revoked-message stubs) that the webhook handler used to
# post as empty messages. The handler now skips them; this clears the ones already stored.
import logging
import re

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    messages = env['whatsapp.message'].search([('wa_account_id.provider', '=', 'waha')])
    phantom_wa = env['whatsapp.message']
    phantom_mail = env['mail.message']
    for wa_message in messages:
        mail_message = wa_message.mail_message_id
        if not mail_message:
            continue
        if re.sub(r'<[^>]*>', '', mail_message.body or '').strip() or mail_message.attachment_ids:
            continue
        phantom_wa |= wa_message
        phantom_mail |= mail_message
    if not phantom_mail:
        return
    count = len(phantom_mail)
    phantom_wa.unlink()          # drop the whatsapp.message first (FK to mail_message)
    phantom_mail.unlink()
    _logger.info("WAHA: removed %s phantom empty message(s)", count)
