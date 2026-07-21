# Re-attribute IMPORTED (phone-sent) outbound WAHA messages to OdooBot.
#
# They used to be credited to the account's FIRST Default User, so the whole shared
# WhatsApp history was falsely attributed to a single employee (and shifted as that list
# changed). WhatsApp gives no sender identity for messages sent outside Odoo, so OdooBot is
# the honest author. ONLY imported messages are touched (subtype = mail.mt_note); messages
# actually sent through Odoo (mt_comment) keep their real author.
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    odoobot = env.ref('base.partner_root', raise_if_not_found=False)
    note = env.ref('mail.mt_note', raise_if_not_found=False)
    if not odoobot or not note:
        return
    wa_messages = env['whatsapp.message'].search([
        ('wa_account_id.provider', '=', 'waha'), ('message_type', '=', 'outbound')])
    mails = wa_messages.mapped('mail_message_id').filtered(
        lambda m: m.subtype_id.id == note.id and m.author_id.id != odoobot.id)
    if not mails:
        return
    count = len(mails)
    mails.write({'author_id': odoobot.id})
    _logger.info("WAHA: re-attributed %s imported outbound message(s) to OdooBot", count)
