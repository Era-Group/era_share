# Seed WAHA notification participants from historical internal replies/notes.
# This does not create retrospective notifications or unanswered-message escalations.
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for account in env['whatsapp.account'].search([('provider', '=', 'waha')]):
        account._waha_backfill_notification_participants()
