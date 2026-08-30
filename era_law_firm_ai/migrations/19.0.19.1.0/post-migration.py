"""Normalise the register's two progress flags.

A boolean column added to existing rows arrives NULL. The ORM reads that as False,
so nothing behaves wrongly, but the register exists to be reported on — "how many
of the sixty-nine still owe a title" — and NULL makes those counts silently wrong
in plain SQL.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE legal_legislation
        SET title_confirmed = COALESCE(title_confirmed, false),
            source_attached = COALESCE(source_attached, false)
        WHERE title_confirmed IS NULL OR source_attached IS NULL
    """)
    if cr.rowcount:
        _logger.info('era_law_firm_ai: normalised the progress flags on %s register row(s)', cr.rowcount)
