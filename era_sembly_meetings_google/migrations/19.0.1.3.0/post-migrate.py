# -*- coding: utf-8 -*-
"""Re-derive started_at again, now that a zone NAME is read as well as an offset.

19.0.1.2.0 taught MEET_NAME_TIME the six spellings of Meet's GMT stamp. This
one adds the recordings Meet stamped with the zone's NAME instead — "AST",
"EEST", "EET" — which is 277 more of this workspace's files. Until now they
fell through to Drive's createdTime, the moment the UPLOAD finished, exactly as
the unparsed GMT spellings did: the record lands at the wrong hour, can fall
outside the "today or yesterday" window the chatter cron posts from, and sits
outside the +/-20 minute window adoption looks through.

The offsets are measured, not looked up in a table of world timezones, which
would be guessing — AST is Arabia Standard Time (+3) here and Atlantic Standard
Time (-4) elsewhere. Read as +3 the 205 AST recordings follow their upload by
15 minutes at the shortest and not one is negative; read as -4, 203 of the 205
finish before they started, which is impossible.

Same guarantees as 19.0.1.2.0, and the same code does the work: only google:
records are touched, a record matched to a Sembly meeting keeps the start Sembly
gave it, and a name that parses to a time AFTER the upload already stored is
left alone and logged rather than trusted. Re-running is a no-op — the rows
19.0.1.2.0 already corrected now parse to the value they already hold.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Meeting = env['sembly.meeting']

    meetings = Meeting.with_context(active_test=False).search([
        ('sembly_meeting_id', '=like', 'google:%'),
    ])
    if not meetings:
        return

    # tracking_disable: this is a correction of imported data, not a human
    # editing a meeting. Without it every record posts a "start time changed"
    # message into its own chatter — a thousand notifications saying nothing.
    meetings = meetings.with_context(sembly_sync=True, tracking_disable=True)

    fixed = late = skipped = 0
    for meeting in meetings:
        started = Meeting._meeting_start_from_name(meeting.name)
        if not started or started == meeting.started_at:
            continue
        # The value being replaced is an upload time, and an upload never
        # precedes its meeting. A derived start LATER than it means the name
        # was misread, so leave the record alone and say so.
        if meeting.started_at and started > meeting.started_at:
            late += 1
            _logger.warning(
                "Sembly/Google: %s parses to %s, after the %s already stored — "
                "left untouched", meeting.name, started, meeting.started_at)
            continue
        meeting.started_at = started
        fixed += 1
        if fixed % 200 == 0:
            env.flush_all()

    skipped = len(meetings) - fixed - late
    _logger.info(
        "Sembly/Google: %s recording(s) moved from their upload time to the "
        "meeting time in their file name, %s already correct or carrying no "
        "timestamp, %s left alone as unparseable", fixed, skipped, late)
