"""Move the monthly corpus sync from the 1st to the 2nd.

The data file is not noupdate, so an upgrade re-asserts nextcall from the XML
— but that expression always lands on next month's 2nd, which would skip a run
when the upgrade happens after the 2nd. Compute the true next occurrence here
instead.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)

RUN_DAY = 2


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('era_law_firm_ai.cron_moj_corpus_sync', raise_if_not_found=False)
    if not cron:
        return
    now = fields.Datetime.now()
    nextcall = now.replace(day=RUN_DAY, hour=2, minute=0, second=0, microsecond=0)
    if nextcall <= now:
        nextcall += relativedelta(months=1)
    cron.write({
        'interval_number': 1,
        'interval_type': 'months',
        'nextcall': nextcall,
    })
    _logger.info('Corpus sync anchored to day %s; next run %s', RUN_DAY, nextcall)
