"""Audit stays WEEKLY; a new DAILY cron clears the safe-fix backlog.

- Defensively ensure the weekly audit cron (cron_weekly_audit_and_fix) is
  weekly (an earlier iteration briefly flipped it to daily).
- Start the new daily fix-only cron (cron_daily_ai_fix, created from the data
  XML in this upgrade) within a few minutes so the OG-image / schema backlog
  begins clearing today instead of on its default first slot.
Both records are noupdate="1", so these one-time nudges live here.
"""


def _cron_id(cr, xmlid_name):
    cr.execute(
        "SELECT res_id FROM ir_model_data "
        "WHERE model='ir.cron' AND module='era_seo_suite' AND name=%s",
        (xmlid_name,),
    )
    row = cr.fetchone()
    return row[0] if row else None


def migrate(cr, version):
    weekly = _cron_id(cr, 'cron_weekly_audit_and_fix')
    if weekly:
        cr.execute(
            "UPDATE ir_cron SET interval_number=1, interval_type='weeks' "
            "WHERE id=%s",
            (weekly,),
        )

    daily = _cron_id(cr, 'cron_daily_ai_fix')
    if daily:
        cr.execute(
            "UPDATE ir_cron SET interval_number=1, interval_type='days', "
            "nextcall=(now() AT TIME ZONE 'UTC') + interval '5 minutes' "
            "WHERE id=%s",
            (daily,),
        )
