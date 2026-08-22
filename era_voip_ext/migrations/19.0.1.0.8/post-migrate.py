"""Collapse legacy dual transcripts into one validated output."""
import logging
import re


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT id, transcript FROM voip_call WHERE strpos(transcript, %s) > 0",
        ["\n ------------ \n"],
    )
    rows = cr.fetchall()
    formatted_count = 0
    for call_id, transcript in rows:
        formatted, raw = transcript.split("\n ------------ \n", 1)
        formatted = formatted.strip().replace("**", "")
        raw = raw.strip()
        complete = (
            len(formatted.strip(" .")) > 10
            and re.search(r"(?m)^\s*الموظف\s*:", formatted)
            and re.search(r"(?m)^\s*العميل\s*:", formatted)
            and (not raw or len(formatted) >= len(raw) * 0.5)
        )
        selected = formatted if complete else (raw or formatted)
        formatted_count += int(bool(complete))
        cr.execute(
            "UPDATE voip_call SET transcript = %s WHERE id = %s",
            [selected, call_id],
        )
    _logger.info(
        "Collapsed %d legacy duplicated VoIP transcripts (%d formatted, %d raw)",
        len(rows), formatted_count, len(rows) - formatted_count,
    )
