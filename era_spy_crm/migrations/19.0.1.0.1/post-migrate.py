# -*- coding: utf-8 -*-
"""Drop the 1-minute poll on the lead auto-enrich cron.

Measured over 24 h on 2026-07-30: this cron ran 1,430 times a day to service a
median of 17 new leads — an 84:1 poll-to-work ratio — and together with its
era_spy_recruitment twin accounted for 53.8% of every cron execution on the
platform (2,859 of 5,318).

CrmLead.create() now calls cron._trigger(at=scheduled_at), so the scheduler is
told exactly when the work is due and the interval below is only a safety net.

This has to be a migration rather than an XML edit: the record was declared
under noupdate="1", which sets ir_model_data.noupdate = True, and Odoo then
skips the record on every module update no matter which data file declares it.
Clearing that flag as well makes data/ir_cron.xml authoritative from here on.
"""

XMLID = ("era_spy_crm", "ir_cron_eraspy_auto_enrich_leads")


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        UPDATE ir_cron c
           SET interval_number = 1,
               interval_type = 'hours'
          FROM ir_model_data d
         WHERE d.model = 'ir.cron'
           AND d.module = %s
           AND d.name = %s
           AND d.res_id = c.id
        """,
        XMLID,
    )
    updated = cr.rowcount

    cr.execute(
        """
        UPDATE ir_model_data
           SET noupdate = false
         WHERE model = 'ir.cron' AND module = %s AND name = %s
        """,
        XMLID,
    )

    if not updated:
        # Nothing to do on a database where the cron was already removed.
        return
