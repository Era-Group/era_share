"""Give the shipped agents their default data selection.

The catalogue and the agent records are noupdate, so an install that already had
the agents would never pick these up. Only empty selections are filled, which
leaves a firm's own choices alone.
"""

import logging

_logger = logging.getLogger(__name__)

DEFAULTS = {
    'agent_contract_review': ['field_case_name', 'field_case_type', 'field_document_text'],
    'agent_drafting': ['field_case_name', 'field_case_najiz', 'field_case_type',
                       'field_case_jurisdiction', 'field_case_court', 'field_case_circuit',
                       'field_case_city'],
    'agent_summary': ['field_case_name', 'field_case_type', 'field_document_text'],
    'agent_research': ['field_case_type', 'field_case_jurisdiction'],
}


def migrate(cr, version):
    if not version:
        return

    def resolve(name):
        cr.execute("SELECT res_id FROM ir_model_data WHERE module='era_law_firm_ai' AND name=%s", (name,))
        row = cr.fetchone()
        return row[0] if row else None

    filled = 0
    for agent_xmlid, field_xmlids in DEFAULTS.items():
        agent_id = resolve(agent_xmlid)
        if not agent_id:
            continue
        cr.execute("SELECT 1 FROM ai_agent_legal_field_rel WHERE agent_id=%s LIMIT 1", (agent_id,))
        if cr.fetchone():
            continue
        ids = [resolve(name) for name in field_xmlids]
        for field_id in [i for i in ids if i]:
            cr.execute(
                "INSERT INTO ai_agent_legal_field_rel (agent_id, field_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING", (agent_id, field_id))
        filled += 1

    if filled:
        _logger.info('era_law_firm_ai: set the default data selection on %s agent(s)', filled)
