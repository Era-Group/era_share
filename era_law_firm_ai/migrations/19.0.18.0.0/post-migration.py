"""Drop the per-statute links.

Each row carried a deep link into the Ministry's legislation portal. They no
longer resolve, and they were never any use to an agent, which cannot open a URL
at all -- so a dead link on every row was two kinds of misleading at once: it
looked like a source the agent could reach, and it did not work for the human
either.

One portal is now named once, on the charter, as the sole authority. It reaches
every request through the standing instructions, and it is where a lawyer goes to
verify a citation before attaching the text as a source.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'legal_legislation' AND column_name = 'url'
    """)
    if not cr.fetchone():
        return

    cr.execute("SELECT count(*) FROM legal_legislation WHERE url IS NOT NULL")
    dropped = cr.fetchone()[0]

    cr.execute("ALTER TABLE legal_legislation DROP COLUMN url")
    cr.execute("DELETE FROM ir_model_fields WHERE model = 'legal.legislation' AND name = 'url'")

    # every existing charter gets the portal it should have had
    cr.execute("""
        UPDATE legal_ai_charter SET reference_portal = 'https://laws.moj.gov.sa/'
        WHERE reference_portal IS NULL OR reference_portal = ''
    """)

    _logger.info('era_law_firm_ai: removed %s dead per-statute link(s); the portal is now named '
                 'once on the charter', dropped)
