"""Collapse legacy dual transcripts into one validated output."""
import logging


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        r"""
        WITH parts AS (
            SELECT id,
                   replace(
                       left(transcript, strpos(transcript, E'\n ------------ \n') - 1),
                       '**', ''
                   ) AS formatted,
                   substring(
                       transcript
                       FROM strpos(transcript, E'\n ------------ \n')
                            + length(E'\n ------------ \n')
                   ) AS raw
              FROM voip_call
             WHERE strpos(transcript, E'\n ------------ \n') > 0
        )
        UPDATE voip_call call
           SET transcript = CASE
               WHEN length(btrim(parts.formatted, ' .')) > 10
                AND parts.formatted ~ '(^|\n)[[:space:]]*الموظف[[:space:]]*:'
                AND parts.formatted ~ '(^|\n)[[:space:]]*العميل[[:space:]]*:'
                AND (parts.raw = ''
                     OR length(parts.formatted) >= length(parts.raw) * 0.5)
               THEN btrim(parts.formatted)
               ELSE COALESCE(NULLIF(btrim(parts.raw), ''), btrim(parts.formatted))
           END
          FROM parts
         WHERE call.id = parts.id
        """
    )
    _logger.info("Collapsed %d legacy duplicated VoIP transcripts", cr.rowcount)
