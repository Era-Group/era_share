"""Fold legal.ai.provider into ai.agent, then take the table out.

The provider record predates Odoo's own AI agent. Once ai.agent became the
transport, the provider's remaining fields described things it could not know:
the LLM -- and therefore where the data is processed -- is chosen per agent, so a
provider sitting in front of several agents could only ever be right about one of
them. Approval, processing location, retention and the classification ceiling all
move onto the agent, and the provider goes.

Runs post-install so the new ai_agent columns already exist while the old
legal_ai_provider table is still there to be read.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT to_regclass('public.legal_ai_provider')")
    if not cr.fetchone()[0]:
        return

    cr.execute("""
        SELECT id, agent_id, approved, processing_location, retention_policy, allowed_classifications
        FROM legal_ai_provider
    """)
    providers = cr.fetchall()

    moved = 0
    for _pid, agent_id, approved, location, retention, classifications in providers:
        if not agent_id:
            continue
        cr.execute("""
            UPDATE ai_agent SET
                legal_approved = %s,
                legal_processing_location = COALESCE(legal_processing_location, %s),
                legal_retention_policy = COALESCE(legal_retention_policy, %s),
                legal_allowed_classifications = COALESCE(legal_allowed_classifications, %s)
            WHERE id = %s
        """, (bool(approved), location, retention, classifications or 'public,internal', agent_id))
        moved += 1

    # requests carried both; keep the agent, and fall back to the provider's one
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'legal_ai_request' AND column_name = 'provider_id'
    """)
    if cr.fetchone():
        cr.execute("""
            UPDATE legal_ai_request r SET agent_id = p.agent_id
            FROM legal_ai_provider p
            WHERE r.provider_id = p.id AND r.agent_id IS NULL AND p.agent_id IS NOT NULL
        """)
        cr.execute("ALTER TABLE legal_ai_request DROP COLUMN provider_id")

    # a request with no agent at all cannot be honoured any more; park it
    cr.execute("UPDATE legal_ai_request SET state = 'cancelled' WHERE agent_id IS NULL AND state NOT IN ('done', 'cancelled')")
    cr.execute("DELETE FROM legal_ai_request WHERE agent_id IS NULL AND state = 'cancelled' AND sanitized_response IS NULL")

    # a NULL boolean reads as False in the ORM but confuses plain SQL reporting
    cr.execute("UPDATE ai_agent SET legal_approved = false WHERE legal_approved IS NULL")

    cr.execute("DELETE FROM ir_model_data WHERE model = 'legal.ai.provider'")
    cr.execute("DELETE FROM ir_model_fields WHERE model = 'legal.ai.provider'")
    cr.execute("DELETE FROM ir_model WHERE model = 'legal.ai.provider'")
    cr.execute("DROP TABLE IF EXISTS legal_ai_provider CASCADE")

    _logger.info('era_law_firm_ai: folded %s provider(s) into their agents and removed the model', moved)
