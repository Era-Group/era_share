# Remove the obsolete "— Imported earlier conversation —" separator notes. WAHA channels
# are now ordered by message date (thread_patch.js), so imported messages slot into their
# correct chronological place and the separator is misplaced/confusing. Delete the ones
# already posted (OdooBot notification notes in WAHA channels carrying that text).
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

MARKERS = ('Imported earlier conversation', 'استيراد المحادثة السابقة')


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    channels = env['discuss.channel'].search([
        ('channel_type', '=', 'whatsapp'), ('wa_account_id.provider', '=', 'waha')])
    if not channels:
        return
    odoobot = env.ref('base.partner_root', raise_if_not_found=False)
    domain = [('model', '=', 'discuss.channel'), ('res_id', 'in', channels.ids),
              ('message_type', '=', 'notification')]
    if odoobot:
        domain.append(('author_id', '=', odoobot.id))
    seps = env['mail.message'].search(domain).filtered(
        lambda m: any(mk in (m.body or '') for mk in MARKERS))
    if seps:
        n = len(seps)
        seps.unlink()
        _logger.info("WAHA: removed %s obsolete 'imported earlier conversation' separator(s)", n)
