# Speed up the reconcile cron: daily -> every 15 minutes, so messages missed while the
# WAHA session was disconnected are caught up quickly (not once a day). The cron record is
# noupdate="1", so the XML change does not reach existing installs — update it here.
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('era_waha_integration.ir_cron_waha_reconcile', raise_if_not_found=False)
    if cron and (cron.interval_type != 'minutes' or cron.interval_number != 15):
        cron.write({'interval_number': 15, 'interval_type': 'minutes'})
        _logger.info("WAHA: reconcile cron set to every 15 minutes")
