# Refresh active Discuss clients with every retained WAHA channel header.
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for account in env['whatsapp.account'].search([('provider', '=', 'waha')]):
        account._waha_keep_channel_members_pinned(broadcast=False)
