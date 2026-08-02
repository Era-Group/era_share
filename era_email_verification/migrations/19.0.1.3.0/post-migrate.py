"""Re-pair the scheduled action with the push-driven flow.

Batches are now submitted and chained immediately through
``ir.cron._trigger()``, so the scheduled action no longer doubles as the
submit latency and can drop from every 2 minutes to the intended quarter-hour
fallback cadence. The reconcile window moves with it (it must stay below the
interval, see ``_reconcile_stale_minutes``).

Both live behind ``noupdate="1"`` records, so a plain ``-u`` cannot change
them — hence this migration. Values an administrator has deliberately changed
away from the old shipped defaults are left alone.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_OLD_INTERVAL = (2, "minutes")
_NEW_INTERVAL = (15, "minutes")
_RECONCILE_KEY = "era_email_verification.reconcile_stale_minutes"
_OLD_RECONCILE = "15"
_NEW_RECONCILE = "5"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    cron = env.ref(
        "era_email_verification.ir_cron_ev_process", raise_if_not_found=False)
    if cron and (cron.interval_number, cron.interval_type) == _OLD_INTERVAL:
        cron.write({
            "interval_number": _NEW_INTERVAL[0],
            "interval_type": _NEW_INTERVAL[1],
            "name": "Email Verification: reconcile & sweep (fallback)",
        })
        _logger.info(
            "era_email_verification: scheduled action moved to every %d %s "
            "(submission is now trigger-driven).", *_NEW_INTERVAL)

    icp = env["ir.config_parameter"].sudo()
    # Only rewrite the value that was paired with the old 2-minute interval;
    # anything else is an explicit administrator choice.
    if (icp.get_param(_RECONCILE_KEY) or "").strip() == _OLD_RECONCILE:
        icp.set_param(_RECONCILE_KEY, _NEW_RECONCILE)
