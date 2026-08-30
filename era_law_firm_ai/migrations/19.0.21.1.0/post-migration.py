"""Withdraw approval from agents pinned to sources they do not have.

restrict_to_sources is a paragraph appended to the prompt, not a gate. With no
sources attached there is no context to restrict the model to, so it answers from
memory — while the setting reads to a lawyer as a guarantee that it did not. An
approved agent in that state is the most misleading configuration the module can
hold, and the shipped research agent was in it.

Approval is withdrawn rather than the restriction being turned off: the restriction
is what the office asked for, and the missing half is the sources. Attach them and
approve again.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT a.id, p.name
        FROM ai_agent a
        JOIN res_partner p ON p.id = a.partner_id
        WHERE a.legal_approved
          AND a.restrict_to_sources
          AND NOT EXISTS (SELECT 1 FROM ai_agent_source s WHERE s.agent_id = a.id)
    """)
    stranded = cr.fetchall()
    if not stranded:
        return

    cr.execute("UPDATE ai_agent SET legal_approved = false WHERE id IN %s",
               (tuple(a for a, _n in stranded),))
    for _id, name in stranded:
        _logger.warning(
            'era_law_firm_ai: approval withdrawn from "%s" — it answers only from its sources '
            'and has none. Attach them under Sources and approve it again.', name)
