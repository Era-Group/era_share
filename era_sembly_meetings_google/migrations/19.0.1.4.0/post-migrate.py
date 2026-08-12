# -*- coding: utf-8 -*-
"""Turn the hourly Google sync on, once, on databases that already exist.

The data file now ships ``cron_sembly_google_sync`` active, but that only
governs a FRESH install: the record is ``noupdate``, deliberately — 19.0.1.1.0
made it so precisely because an upgrade re-applying the data file's ``active``
had silently stopped a backfill mid-run. So on an existing database the XML
change is a no-op and the cron stays exactly as it was: off, which on this
deployment meant the two backfills imported history while NOTHING picked up the
meetings that happened afterwards.

Hence this: a one-time flip at the version bump. It is a migration and not a
permanent override, so it runs once and never again — somebody who turns the
cron off tomorrow keeps it off, and the noupdate guarantee is untouched.

Safe to run anywhere: _cron_sync_google returns on its first line unless
_google_enabled(), which needs both sembly.google_enabled and a service account
JSON. On a database without credentials this wakes hourly, reads two
parameters, and goes back to sleep.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_cron SET active = TRUE
         WHERE id IN (SELECT res_id FROM ir_model_data
                       WHERE module = 'era_sembly_meetings_google'
                         AND model = 'ir.cron'
                         AND name = 'cron_sembly_google_sync')
           AND active IS NOT TRUE
    """)
    if cr.rowcount:
        _logger.info("Sembly/Google: the hourly Drive sync is now active — new "
                     "recordings and Gemini notes will be picked up without "
                     "anyone having to arm it.")
