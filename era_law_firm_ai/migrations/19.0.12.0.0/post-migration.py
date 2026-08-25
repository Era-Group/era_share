"""Turn the comma-separated classification list into an ordered ceiling.

The old field was free text, so it could not express that the levels are ordered
and it accepted anything typed into it. Worse, the shipped default paired
'public,internal' agents against documents that default to confidential, which
made it impossible to send any document to any agent at all -- a safeguard that
blocks every legitimate use is a misconfiguration, and the first person to hit it
would have widened it without weighing anything.

Whatever a firm had set is preserved by taking the highest level it listed.
"""

import logging

_logger = logging.getLogger(__name__)

ORDER = ['public', 'internal', 'confidential']


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_agent' AND column_name = 'legal_allowed_classifications'
    """)
    if not cr.fetchone():
        return

    cr.execute("SELECT id, legal_allowed_classifications FROM ai_agent")
    for agent_id, listed in cr.fetchall():
        levels = [item.strip() for item in (listed or '').split(',') if item.strip() in ORDER]
        ceiling = max(levels, key=ORDER.index) if levels else 'confidential'
        cr.execute("UPDATE ai_agent SET legal_max_classification = %s WHERE id = %s",
                   (ceiling, agent_id))

    cr.execute("ALTER TABLE ai_agent DROP COLUMN legal_allowed_classifications")
    cr.execute("DELETE FROM ir_model_fields WHERE model = 'ai.agent' "
               "AND name = 'legal_allowed_classifications'")
    _logger.info('era_law_firm_ai: replaced the classification list with an ordered ceiling')
