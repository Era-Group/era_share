"""Remove duplicate harvested conversations before the constraint can exist.

19.0.1.1.0 declared the uniqueness of (thread_model, thread_id) with
_sql_constraints, which Odoo 19 no longer honours: the registry logged a
warning and created no constraint at all, so the hourly harvest cron filed
the same conversation again on every run. 19.0.1.2.0 declared it properly
with models.Constraint -- and on a site carrying those duplicates the
CREATE would fail, Odoo would log another warning, and the table would go
on without the constraint for ever.

So the duplicates have to go first, in a pre-migration, before the ORM
tries to add it. The oldest row of each group survives because whatever
lead or ticket was filed from this conversation points at that one.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.tables
         WHERE table_name = 'era_ai_conversation'
    """)
    if not cr.fetchone():
        return  # the model never existed on this install

    cr.execute("""
        DELETE FROM era_ai_conversation
         WHERE id NOT IN (
               SELECT MIN(id)
                 FROM era_ai_conversation
                GROUP BY thread_model, thread_id
         )
    """)
    # Read it before the next statement: cr.rowcount belongs to whichever
    # query ran last, so asking after the SELECT below reports the count of
    # the SELECT and understates what was deleted.
    removed = cr.rowcount
    if removed:
        cr.execute("SELECT COUNT(*) FROM era_ai_conversation")
        _logger.info(
            "era_ai_manager: removed %s duplicate conversation(s), %s remain",
            removed, cr.fetchone()[0])
