"""Move the corpus sync from weekly to the first of each month.

The scraper republishes every Saturday, but statutes do not change at that
rate. A run that finds nothing does one fetch and stops; a run that finds
something re-embeds only the statutes whose text actually moved. Weekly bought
freshness nobody was waiting for.

Odoo adds the interval to nextcall rather than to a calendar anchor, so the
date has to be set explicitly or the job drifts to whatever day the upgrade
happened to run.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('era_law_firm_ai.cron_moj_corpus_sync', raise_if_not_found=False)
    if not cron:
        return
    now = fields.Datetime.now()
    first = now.replace(day=1, hour=2, minute=0, second=0, microsecond=0)
    if first <= now:
        first += relativedelta(months=1)
    cron.write({
        'interval_number': 1,
        'interval_type': 'months',
        'nextcall': first,
    })
    _logger.info('Corpus sync is now monthly; next run %s', first)
