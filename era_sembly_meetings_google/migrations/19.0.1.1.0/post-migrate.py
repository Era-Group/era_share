# -*- coding: utf-8 -*-
"""Make the runtime-armed crons survive future upgrades.

``noupdate="1"`` in the data file only governs records Odoo CREATES from it.
The rows here already exist with noupdate = false from the first install, and
the XML upsert never rewrites that flag — the same trap the README records for
the employee record rule.

So until this runs, every upgrade re-applies ``active="False"`` and silently
stops whatever was running. It happened: deploying an unrelated change disarmed
the Gemini notes backfill mid-run, and it sat at 53 of 398 until the monitor
showed the count had stopped moving.
"""
import logging

_logger = logging.getLogger(__name__)

# Names taken from data/ir_cron.xml, not from memory — the sync one is
# cron_sembly_google_sync, and guessing it cost a test failure.
CRONS = ('cron_sembly_google_sync', 'cron_google_backfill',
         'cron_google_notes_backfill')


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_data SET noupdate = TRUE
         WHERE module = 'era_sembly_meetings_google'
           AND model = 'ir.cron'
           AND name IN %s
           AND noupdate IS NOT TRUE
    """, (CRONS,))
    if cr.rowcount:
        _logger.info("Sembly/Google: %s cron(s) marked noupdate so their "
                     "armed state survives an upgrade", cr.rowcount)
