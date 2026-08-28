"""Drop conversations nobody can be answered on.

Until 19.0.1.3.0 every finished chat was filed, including the anonymous
visitors who left no address and no phone number. Those rows cannot be
answered, converted or followed up: opening one only ever ends in closing
it again, and a review list full of them teaches people to stop reading it.

New harvests already skip them. This clears the ones an older version
filed. The conversations themselves are untouched -- they still count
towards the broken-assistant check, which reads the channels directly for
exactly this reason.

A row that already produced a lead or a ticket is kept whatever it looks
like now: something downstream refers to it, and deleting the source of a
record someone is working on is not a clean-up.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.tables
         WHERE table_name = 'era_ai_conversation'
    """)
    if not cr.fetchone():
        return

    cr.execute("""
        DELETE FROM era_ai_conversation
         WHERE COALESCE(email, '') = ''
           AND COALESCE(phone, '') = ''
           AND partner_id IS NULL
           AND COALESCE(result_id, 0) = 0
    """)
    if cr.rowcount:
        _logger.info(
            "era_ai_manager: removed %s conversation(s) that could not be "
            "answered on", cr.rowcount)
