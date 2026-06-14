"""Backfill groundwork for posting call analysis to lead chatter.

Earlier versions only linked a call to a lead via ``partner_id``, so the vast
majority of analysed calls never had their analysis posted to a lead's chatter.
This release adds phone-number matching plus a backfill cron. To avoid the
backfill creating duplicate chatter notes, mark every call whose analysis was
already posted as ``lead_post_status = 'posted'`` here, then trigger the cron.
"""
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Matches the call link embedded in legacy analysis notes, e.g.
# "id=2425&amp;model=voip.call" (the '&' is HTML-escaped in stored bodies).
_CALL_LINK_RE = re.compile(r"id=(\d+)&(?:amp;)?model=voip\.call")


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1. Map calls already posted to a lead -> that lead, from existing notes.
    cr.execute(
        """
        SELECT res_id, body
          FROM mail_message
         WHERE model = 'crm.lead'
           AND subject LIKE %s
           AND body LIKE %s
        """,
        ("%تحليل مكالمة%", "%voip.call%"),
    )
    posted = {}  # call_id -> lead_id (first/most relevant note wins)
    for lead_id, body in cr.fetchall():
        if not body or not lead_id:
            continue
        for match in _CALL_LINK_RE.finditer(body):
            posted.setdefault(int(match.group(1)), lead_id)

    if posted:
        call_ids = list(posted.keys())
        # A legacy note in chatter is ground truth that the call was already
        # posted — mark it regardless of the call's current analysis_status, so
        # a later re-analysis cannot create a duplicate note.
        cr.execute(
            """
            UPDATE voip_call
               SET lead_post_status = 'posted'
             WHERE id = ANY(%s)
            """,
            (call_ids,),
        )
        # Record which lead received it (only where not already set).
        for call_id, lead_id in posted.items():
            cr.execute(
                """
                UPDATE voip_call
                   SET analysis_lead_id = %s
                 WHERE id = %s
                   AND analysis_lead_id IS NULL
                """,
                (lead_id, call_id),
            )
        _logger.info(
            "era_voip_ext: marked %d call(s) as already posted to a lead", len(posted)
        )

    # 2. Calls whose analysis was skipped have nothing to post.
    cr.execute(
        """
        UPDATE voip_call
           SET lead_post_status = 'skipped'
         WHERE analysis_status = 'skipped'
           AND lead_post_status = 'pending'
        """
    )

    # 3. Drain the remaining 'done' + 'pending' backlog promptly.
    cron = env.ref(
        "era_voip_ext.ir_cron_post_pending_analysis_to_lead",
        raise_if_not_found=False,
    )
    if cron:
        cron.sudo()._trigger()
