# Backfill channel membership for existing WAHA "Default Users" (notify_user_ids).
# The runtime sync (whatsapp_account.write/_waha_apply_channel_members) only fires when
# notify_user_ids CHANGES, so on a DB where accounts already had Default Users before the
# membership feature (or the module was just updated), those users may not yet be members
# of the account's existing channels — meaning they can't SEE the conversations. This
# one-shot, idempotent backfill guarantees every current Default User is a member of every
# channel of their WAHA account after the upgrade. Per-account fail-safe so one bad account
# never aborts the -u.
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    accounts = env['whatsapp.account'].search([('provider', '=', 'waha')])
    granted = 0
    for account in accounts:
        if not account.notify_user_ids:
            continue
        try:
            account._waha_apply_channel_members(account.notify_user_ids, env['res.users'])
            granted += 1
        except Exception:
            _logger.exception("WAHA: member-access backfill failed for account %s", account.id)
    _logger.info("WAHA: member-access backfill ran for %s account(s)", granted)
