"""Carry the follow-up chain into dispatch history, then drop it.

Asking again used to open a new request linked back to the one it followed. It now
reopens the request itself and keeps the previous dispatch as history, so the
chain has nothing left to describe. Anything already dispatched on a follow-up is
preserved as an attempt on its origin rather than thrown away.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'legal_ai_request' AND column_name = 'origin_id'
    """)
    if not cr.fetchone():
        return

    cr.execute("""
        SELECT id, origin_id, agent_id, charter_id, write_date, consent_user_id, consent_date,
               fields_sent, instructions_sent, redacted_payload, payload_hash, sanitized_response
        FROM legal_ai_request
        WHERE origin_id IS NOT NULL AND payload_hash IS NOT NULL
        ORDER BY id
    """)
    moved = 0
    for row in cr.fetchall():
        (_rid, origin_id, agent_id, charter_id, written, consent_uid, consent_date,
         fields_sent, instructions, payload, digest, response) = row
        cr.execute("SELECT COALESCE(MAX(sequence_number), 0) + 1 FROM legal_ai_attempt WHERE request_id = %s",
                   (origin_id,))
        number = cr.fetchone()[0]
        cr.execute("""
            INSERT INTO legal_ai_attempt
                (request_id, sequence_number, agent_id, charter_id, sent_at, consent_user_id,
                 consent_date, fields_sent, instructions_sent, redacted_payload, payload_hash,
                 sanitized_response, create_uid, write_uid, create_date, write_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1, now(), now())
        """, (origin_id, number, agent_id, charter_id, written, consent_uid, consent_date,
              fields_sent, instructions, payload, digest, response))
        moved += 1

    cr.execute("ALTER TABLE legal_ai_request DROP COLUMN origin_id")
    cr.execute("DELETE FROM ir_model_fields WHERE model = 'legal.ai.request' AND name IN ('origin_id', 'followup_ids', 'followup_count')")
    _logger.info('era_law_firm_ai: moved %s follow-up dispatch(es) into history and removed the chain', moved)
