# Preserve every WAHA conversation in the Discuss sidebar after the shared-inbox
# policy was introduced. The data already exists; only its sidebar pin state changed.
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    accounts = env['whatsapp.account'].search([('provider', '=', 'waha')])
    for account in accounts:
        account._waha_keep_channel_members_pinned(broadcast=False)
