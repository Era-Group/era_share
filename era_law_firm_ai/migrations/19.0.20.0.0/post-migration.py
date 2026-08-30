"""Remove the legislation register.

It held sixty-nine references, none of which an agent could read. What an agent
reads is text attached to it as an ai.agent.source, and the register's
"text attached" flag was a manual mirror of exactly that — state Odoo already
holds authoritatively, kept by hand, which drifts the first time someone attaches
or removes a source without ticking the box.

The office's list is not lost: it is preserved verbatim in
docs/legislation_references.md, as a document rather than a model, because a
document is what it always was.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT to_regclass('public.legal_legislation')")
    if not cr.fetchone()[0]:
        return

    cr.execute("SELECT count(*) FROM legal_legislation")
    rows = cr.fetchone()[0]

    for table in ('ai_agent_legislation_rel', 'legal_ai_charter_legal_legislation_rel'):
        cr.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    cr.execute("DROP TABLE IF EXISTS legal_legislation CASCADE")

    cr.execute("DELETE FROM ir_model_data WHERE model = 'legal.legislation'")
    cr.execute("DELETE FROM ir_model_fields WHERE model = 'legal.legislation'")
    cr.execute("DELETE FROM ir_model_fields WHERE model IN ('ai.agent', 'legal.ai.charter') "
               "AND name IN ('legal_legislation_ids', 'legislation_ids', 'legal_sources_pending')")
    cr.execute("DELETE FROM ir_model WHERE model = 'legal.legislation'")

    _logger.info('era_law_firm_ai: removed the legislation register (%s row(s)); the references '
                 'are kept in docs/legislation_references.md', rows)
