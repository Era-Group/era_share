# -*- coding: utf-8 -*-
"""Re-read the meeting start out of every Drive file name we could not parse.

``MEET_NAME_TIME`` used to match ONE spelling of Meet's timestamp,
``YYYY/MM/DD HH:MM GMT+03:00``. Drive holds six. On this workspace that one
pattern covered 1 344 of 3 155 recordings; the other 1 146 fell through to
Drive's ``createdTime`` — the moment the UPLOAD finished, not the moment the
meeting happened. Measured against the names they carried all along, those
records sit a median of 68 minutes late, p90 four hours.

Two things were broken by that, and neither is cosmetic:

* the record lands at the wrong hour, so it can fall outside the "today or
  yesterday" window the chatter cron posts from;
* ``_adopt_orphan_google_record`` looks ±``sembly.google_match_minutes``
  (20 by default) around the Sembly meeting's start, and 1 024 of the 1 146
  sat outside it — so when Sembly finally delivered the same meeting, the
  recording could not be folded into it and the meeting stayed split across
  two records, the recording stranded on the one with no summary and no links.

Only ``google:`` records are touched. A record that matched a Sembly meeting
takes its start from Sembly, which is authoritative, and must not be rewritten
from a file name.

Verified against this workspace's 3 155 names before shipping: the widened
pattern changes NO time the old one already produced, and every newly derived
start precedes the upload time that was stored in its place — an upload never
precedes its meeting, so a single violation would have meant a misparse.
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
