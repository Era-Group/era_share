"""Flip the 'audit + AI auto-fix' cron from weekly to daily on existing
installs.

The cron record (xmlid era_seo_suite.cron_weekly_audit_and_fix) is defined
with noupdate="1", so the interval change in data/ir_cron_weekly_audit.xml
only reaches FRESH installs. This migration updates the already-installed
record and brings its next run forward so the warning backlog (OG image /
schema) starts clearing within the day instead of waiting for the old weekly
slot.
"""


def migrate(cr, version):
    cr.execute(
        """
        SELECT c.id, c.nextcall
        FROM ir_cron c
        JOIN ir_model_data d ON d.model = 'ir.cron' AND d.res_id = c.id
        WHERE d.module = 'era_seo_suite'
          AND d.name = 'cron_weekly_audit_and_fix'
        """
    )
    row = cr.fetchone()
    if not row:
        return
    cron_id = row[0]
    # Weekly -> daily.
    cr.execute(
        "UPDATE ir_cron SET interval_number = 1, interval_type = 'days' "
        "WHERE id = %s",
        (cron_id,),
    )
    # If the next run is more than a day out (the old weekly slot), pull it in
    # to ~1 hour from now so the backlog begins clearing promptly.
    cr.execute(
        """
        UPDATE ir_cron
        SET nextcall = (now() AT TIME ZONE 'UTC') + interval '1 hour'
        WHERE id = %s
          AND nextcall > (now() AT TIME ZONE 'UTC') + interval '1 day'
        """,
        (cron_id,),
    )
