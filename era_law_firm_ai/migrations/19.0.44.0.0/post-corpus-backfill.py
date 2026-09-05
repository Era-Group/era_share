"""Give this version's new agent the corpus the others already carry.

19.0.44.0.0 ships the Legal Advisor, flagged as a corpus target. Flagging
alone attaches nothing: the scheduled sync skips a corpus that has not
changed upstream, so without this the new agent would answer with no statute
to cite until the second of next month. No download and no embedding is
needed — the text is in the database, and chunks are keyed by the file's
checksum, so attaching it again is recognised at once.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    attached = env['moj.law']._backfill_target_agents()
    _logger.info('era_law_firm_ai: corpus backfill attached %s source(s).', attached)
