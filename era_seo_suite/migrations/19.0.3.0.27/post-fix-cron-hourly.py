"""Flip the AI auto-fix cron (cron_daily_ai_fix) from daily to HOURLY on
existing installs.

Safe to run frequently: each tick is bounded by both _UNATTENDED_FIX_BATCH and
a per-tick wall-clock budget, only touches ai_status='none' findings (so no
reprocessing), and is a single no-op search once the backlog is clear. The
record is noupdate="1", so the interval change in data/ir_cron_weekly_audit.xml
only reaches fresh installs — this updates the live record.
"""


def migrate(cr, version):
    cr.execute(
        "SELECT res_id FROM ir_model_data "
        "WHERE model='ir.cron' AND module='era_seo_suite' AND name='cron_daily_ai_fix'"
    )
    row = cr.fetchone()
    if not row:
        return
    cron_id = row[0]
    cr.execute(
        "UPDATE ir_cron SET interval_number=1, interval_type='hours', "
        "nextcall=(now() AT TIME ZONE 'UTC') + interval '5 minutes' "
        "WHERE id=%s",
        (cron_id,),
    )
    # The cron's display name lives on the linked server action (noupdate=1,
    # so the data XML rename doesn't reach the existing record). Refresh it.
    cr.execute(
        "UPDATE ir_act_server SET name = jsonb_set("
        "COALESCE(name, '{}'::jsonb), '{en_US}', "
        "'\"ERA SEO: Hourly AI auto-fix\"') "
        "WHERE id = (SELECT ir_actions_server_id FROM ir_cron WHERE id=%s)",
        (cron_id,),
    )
