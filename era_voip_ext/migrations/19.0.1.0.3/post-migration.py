"""Remove the one-time analysis backfill cron.

The phone-based lead resolution and idempotent posting are now permanent and
run inline from `_analyze_call` for every new call, so the backfill cron has
served its purpose. Its record is `noupdate`, so dropping it from the data
file does not delete it automatically — remove it (and its external id) here.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    imd = env["ir.model.data"].search(
        [
            ("module", "=", "era_voip_ext"),
            ("name", "=", "ir_cron_post_pending_analysis_to_lead"),
        ]
    )
    cron = env["ir.cron"].browse(imd.mapped("res_id")).exists()
    if cron:
        cron.unlink()
        _logger.info("era_voip_ext: removed obsolete analysis backfill cron")
    if imd:
        imd.unlink()
