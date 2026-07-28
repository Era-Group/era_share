# Part of Era Group custom addons.
"""Seed the hardening settings introduced in 19.0.1.19.0 on existing WAHA accounts.

New columns get their field default automatically, but accounts that were already
tuned by hand deserve values consistent with what the 2026-07-28 incident showed:
several agents sharing one session first-contacted up to 19 strangers a day while the
per-user cap read 2, and the session cycled ~70 times in the final hour with nothing
holding the queue back.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    accounts = env['whatsapp.account'].search([('provider', '=', 'waha')])
    for account in accounts:
        vals = {}
        # An account-wide cap only helps if it is tighter than per-user x head-count.
        per_user = account.waha_new_number_daily_limit or 0
        if not account.waha_new_number_account_daily_limit:
            vals['waha_new_number_account_daily_limit'] = max(per_user * 2, 8)
        # Clear any stale pause so the upgrade never leaves the queue held.
        if account.waha_paused_until:
            vals.update(waha_paused_until=False, waha_pause_reason=False)
        if vals:
            account.write(vals)
    _logger.info("WAHA: hardening defaults applied to %s account(s)", len(accounts))
