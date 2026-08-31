from odoo import fields

from . import models


def _schedule_first_corpus_sync(env):
    """Fetch the statutes now rather than at the start of next month.

    The sync is monthly, so a firm installing on the 3rd would wait four weeks
    for its corpus — and until it arrives the research agent is restricted to
    sources it does not have, which its own constraint refuses to approve. The
    install would look finished and the agent would be unusable.

    The cron is asked to run immediately instead of doing the fetch inline: it
    is a 6MB download and an indexing pass, and an install should not block on
    the network.
    """
    cron = env.ref('era_law_firm_ai.cron_moj_corpus_sync', raise_if_not_found=False)
    if cron:
        cron.sudo().write({'nextcall': fields.Datetime.now()})
