# -*- coding: utf-8 -*-
"""Switch the applicant auto-enrich cron off.

Measured over 24 h on 2026-07-30: this cron ran 1,429 times a day and found
nothing to do every single time. hr_applicant holds 69 rows, none of them with
eraspy_auto_enrich_pending set, and the newest was created 2026-01-13 — no new
applicant in over six months. Together with its era_spy_crm twin it accounted
for 53.8% of every cron execution on the platform.

If recruitment starts up again, do not simply re-activate this: give it the
event-driven treatment era_spy_crm now uses (cron._trigger() from create(),
with a long interval as a safety net).

This has to be a migration rather than an XML edit: the record was declared
under noupdate="1", which sets ir_model_data.noupdate = True, and Odoo then
skips the record on every module update no matter which data file declares it.
Clearing that flag as well makes data/ir_cron.xml authoritative from here on.
"""

XMLID = ("era_spy_recruitment", "ir_cron_eraspy_auto_enrich_applicants")


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        UPDATE ir_cron c
           SET active = false
          FROM ir_model_data d
         WHERE d.model = 'ir.cron'
           AND d.module = %s
           AND d.name = %s
           AND d.res_id = c.id
        """,
        XMLID,
    )

    cr.execute(
        """
        UPDATE ir_model_data
           SET noupdate = false
         WHERE model = 'ir.cron' AND module = %s AND name = %s
        """,
        XMLID,
    )
