# -*- coding: utf-8 -*-
"""Requirement 6 — post the LATEST meeting dated today or yesterday as an
INTERNAL note on every record it is linked to, exactly once.

The base app links to nothing, so the tests supply the targets by stubbing the
``_summary_targets`` seam with a ``res.partner`` (which is a ``mail.thread``).
That is the point of the split: this posting machinery is target-agnostic, and
``era_sembly_meetings_crm`` / ``_tasks`` / ``_tickets`` each test their own
target on top of it.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.tests import TransactionCase, tagged

from . import fixtures


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyChatter(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']
        self.env['ir.config_parameter'].sudo().set_param('sembly.timezone', 'Asia/Riyadh')
        # Off by default in THIS suite: the pre-existing tests assert the raw
        # summary path, and must never depend on an LLM. The brief tests turn
        # it on explicitly and stub the agent.
        self.env['ir.config_parameter'].sudo().set_param('sembly.ai_brief_enabled', '0')
        self.target = self.env['res.partner'].create({'name': "Acme Industrial"})

    def _with_targets(self, targets=None):
        """Stub the seam a link module would implement."""
        records = self.target if targets is None else targets
        return patch.object(type(self.Meeting), '_summary_targets',
                            lambda meeting: [records] if records else [])

    def _make(self, sembly_id, hours_ago=2, **kwargs):
        values = {
            'sembly_meeting_id': str(sembly_id),
            'name': "Meeting %s" % sembly_id,
            'started_at': fields.Datetime.now() - timedelta(hours=hours_ago),
            'summary': "<p>We agreed on the rollout plan.</p>",
        }
        values.update(kwargs)
        return self.Meeting.create(values)

    def _notes(self, record):
        """Only the notes THIS feature posted.

        A target record generally carries chatter of its own (creating a
        res.partner already logs one), so counting every internal note would
        measure Odoo, not us. The QWeb template's heading is the marker.
        """
        note = self.env.ref('mail.mt_note')
        return record.message_ids.filtered(
            lambda m: m.subtype_id == note and "Meeting summary" in (m.body or ''))

    def test_posts_internal_note_on_every_target(self):
        meeting = self._make(9001)
        before = len(self._notes(self.target))

        with self._with_targets():
            self.Meeting._cron_post_recent_summaries()

        self.assertTrue(meeting.summary_posted)
        self.assertTrue(meeting.summary_posted_on)
        self.assertEqual(len(self._notes(self.target)), before + 1)
        body = self._notes(self.target)[0].body
        self.assertIn("rollout plan", body)
        # The recording link is part of the note (requirement 1 + 6).
        self.assertIn("Recording", body)

    def test_note_is_internal_not_a_customer_email(self):
        """Meeting content must never reach a record's customer-side followers."""
        self._make(9002)
        with self._with_targets():
            self.Meeting._cron_post_recent_summaries()
        message = self._notes(self.target)[0]
        self.assertEqual(message.subtype_id, self.env.ref('mail.mt_note'))
        self.assertEqual(message.message_type, 'notification')

    def test_idempotent_second_run_posts_nothing(self):
        self._make(9003)
        with self._with_targets():
            self.Meeting._cron_post_recent_summaries()
            count = len(self._notes(self.target))
            self.Meeting._cron_post_recent_summaries()
        self.assertEqual(len(self._notes(self.target)), count)

    def test_old_meeting_is_not_posted(self):
        meeting = self._make(9004, hours_ago=72)
        with self._with_targets():
            self.Meeting._cron_post_recent_summaries()
        self.assertFalse(meeting.summary_posted)
        self.assertFalse(self._notes(self.target))

    def test_only_the_latest_meeting_per_record_is_posted(self):
        """'آخر اجتماع' — the newest one, not every one in the window."""
        older = self._make(9005, hours_ago=20)
        newest = self._make(9006, hours_ago=1)

        with self._with_targets():
            self.Meeting._cron_post_recent_summaries()

        self.assertEqual(len(self._notes(self.target)), 1)
        self.assertIn("Meeting 9006", self._notes(self.target)[0].body)
        self.assertTrue(newest.summary_posted)
        # The older one is marked done so it is never posted late.
        self.assertTrue(older.summary_posted)

    def test_riyadh_evening_meeting_counts_as_today(self):
        """A 21:00 Riyadh meeting is 18:00 UTC — the window must be computed in
        the company timezone, not by naive UTC arithmetic."""
        tz = pytz.timezone('Asia/Riyadh')
        now_local = fields.Datetime.now().replace(tzinfo=pytz.UTC).astimezone(tz)
        local_21 = tz.localize(datetime.combine(
            now_local.date(), datetime.min.time().replace(hour=21)))
        started = local_21.astimezone(pytz.UTC).replace(tzinfo=None)

        domain = self.Meeting._recent_window_domain()
        self.assertGreaterEqual(started, domain[0][2])
        self.assertLessEqual(started, domain[1][2])

    def test_meeting_without_summary_is_not_posted(self):
        meeting = self._make(9007, summary=False)
        with self._with_targets():
            self.Meeting._cron_post_recent_summaries()
        self.assertFalse(meeting.summary_posted)
        self.assertFalse(self._notes(self.target))

    def test_meeting_without_targets_is_not_posted(self):
        """With no link module installed — or nothing linked — there is nowhere
        to post, and the meeting must NOT be marked as posted."""
        meeting = self._make(9008)
        with self._with_targets(targets=False):
            self.Meeting._cron_post_recent_summaries()
        self.assertFalse(meeting.summary_posted)

    def test_manual_button_ignores_the_window(self):
        """An explicit request is an override — the escape hatch for old meetings."""
        meeting = self._make(9009, hours_ago=240)
        with self._with_targets():
            meeting.action_post_summary_to_chatter()
        self.assertTrue(meeting.summary_posted)
        self.assertEqual(len(self._notes(self.target)), 1)

    def test_manual_button_reports_when_nothing_was_posted(self):
        meeting = self._make(9010, summary=False)
        action = meeting.action_post_summary_to_chatter()
        self.assertEqual(action['params']['type'], 'warning')
        self.assertFalse(meeting.summary_posted)

    def test_note_includes_decisions_and_tasks_from_items(self):
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)
        meeting.sudo().with_context(sembly_sync=True).write({
            'started_at': fields.Datetime.now() - timedelta(hours=1),
        })
        with self._with_targets():
            meeting.action_post_summary_to_chatter()
        body = self._notes(self.target)[0].body
        self.assertIn("Rollout starts in September", body)
        self.assertIn("Prepare the migration plan", body)

    # ------------------------------------------------------------------ brief
    BRIEF = ("<h4>حالة العميل</h4><p>متقدم نحو الإغلاق.</p>"
             "<h4>الإيجابيات</h4><ul><li>موافقات مؤكدة</li></ul>"
             "<h4>السلبيات والمخاطر</h4><ul><li>تأخر الدفعات</li></ul>"
             "<h4>نطاق العمل</h4><ul><li>مراجعة الفرص السبع</li></ul>"
             "<h4>الخطوة القادمة</h4><ul><li>اجتماع Excel غداً — قصي</li></ul>")

    def _brief_on(self):
        self.env['ir.config_parameter'].sudo().set_param('sembly.ai_brief_enabled', '1')

    def test_the_note_carries_the_brief_not_the_raw_summary(self):
        self._brief_on()
        meeting = self._make(9101)
        with self._with_targets(), \
                patch.object(type(self.Meeting), '_ask_agent',
                             return_value=self.BRIEF):
            meeting.action_post_summary_to_chatter()
        body = self._notes(self.target)[0].body
        self.assertIn("الخطوة القادمة", body)
        self.assertIn("متقدم نحو الإغلاق", body)
        # The brief REPLACES the raw summary; posting both would double the noise.
        self.assertNotIn("rollout plan", body)
        self.assertIn("متقدم نحو الإغلاق", meeting.ai_brief)

    def test_agent_failure_falls_back_to_the_raw_summary(self):
        """A brief must never block the note."""
        self._brief_on()
        meeting = self._make(9102)
        with self._with_targets(), \
                patch.object(type(self.Meeting), '_ask_agent',
                             side_effect=ValueError("provider down")):
            meeting.action_post_summary_to_chatter()
        body = self._notes(self.target)[0].body
        self.assertIn("rollout plan", body)
        self.assertTrue(meeting.summary_posted)
        self.assertTrue(self.env['sembly.sync.log'].search_count(
            [('channel', '=', 'ai'), ('operation', '=', 'brief'),
             ('state', '=', 'error')]))

    def test_the_brief_is_generated_once_and_reused(self):
        self._brief_on()
        meeting = self._make(9103)
        with self._with_targets(), \
                patch.object(type(self.Meeting), '_ask_agent',
                             return_value=self.BRIEF) as ask:
            meeting.action_post_summary_to_chatter()
            meeting.action_post_summary_to_chatter()
        ask.assert_called_once()

    def test_the_toggle_really_switches_it_off(self):
        meeting = self._make(9104)   # setUp left the toggle off
        with self._with_targets(), \
                patch.object(type(self.Meeting), '_ask_agent') as ask:
            meeting.action_post_summary_to_chatter()
        ask.assert_not_called()
        self.assertIn("rollout plan", self._notes(self.target)[0].body)

    # ------------------------------------------------- webhook -> brief -> post
    def test_a_webhook_arrival_enters_the_pipeline(self):
        """A meeting that just arrived should reach its opportunity in minutes,
        not wait for the hourly sweep.

        Deliberately uses the fixture's own (old) date: Sembly pushes a meeting
        once, when it finishes processing it, so the push IS the freshness
        signal. Gating this on the recent window would drop a note after a
        weekend processing delay, or when a human presses Sembly's "Zap".
        """
        meeting = self.Meeting._upsert_from_webhook(
            dict(fixtures.WEBHOOK_NOTES, meeting_id=770001), 'notes')
        self.assertTrue(meeting.has_summary)
        self.assertTrue(meeting.ai_match_queued,
                        "the arrival must be queued for match + post")

    def test_an_arrival_with_nothing_to_say_is_not_queued(self):
        """A transcription-only webhook carries no summary: queuing it would
        buy two LLM round trips for an empty note."""
        meeting = self.Meeting._upsert_from_webhook(
            dict(fixtures.WEBHOOK_TRANSCRIPTION, meeting_id=770002), 'transcription')
        self.assertFalse(meeting.has_summary)
        self.assertFalse(meeting.ai_match_queued)

    def test_the_queue_generates_the_brief_and_posts_it(self):
        self.env['ir.config_parameter'].sudo().set_param('sembly.ai_brief_enabled', '1')
        meeting = self._make(770010, hours_ago=2, link_state='manual')
        meeting.sudo().with_context(sembly_sync=True).write({'ai_match_queued': True})
        with self._with_targets(), \
                patch.object(type(self.Meeting), '_ask_agent',
                             return_value="<h4>حالة العميل</h4><p>جاهز</p>") as ask:
            self.Meeting._cron_ai_match_queue()
        ask.assert_called_once()          # the brief, not a second match
        self.assertIn("حالة العميل", meeting.ai_brief or '')
        self.assertTrue(meeting.summary_posted)
        notes = self._notes(self.target)
        self.assertEqual(len(notes), 1)
        self.assertIn("حالة العميل", notes[0].body)

    def test_the_queue_does_not_post_old_meetings(self):
        """A bulk re-match of two years of history must not dump a note into
        hundreds of old opportunities."""
        old = self._make(770011, hours_ago=24 * 200, link_state='manual')
        old.sudo().with_context(sembly_sync=True).write({'ai_match_queued': True})
        with self._with_targets(), \
                patch.object(type(self.Meeting), '_ask_agent', return_value="x"):
            self.Meeting._cron_ai_match_queue()
        self.assertFalse(old.summary_posted)
        self.assertFalse(self._notes(self.target))

    def test_a_failed_brief_still_posts_the_raw_summary(self):
        """The brief is an improvement, never a gate."""
        self.env['ir.config_parameter'].sudo().set_param('sembly.ai_brief_enabled', '1')
        meeting = self._make(770012, hours_ago=2)
        with self._with_targets(), \
                patch.object(type(self.Meeting), '_ask_agent',
                             side_effect=ValueError("provider down")):
            meeting._post_summary()
        self.assertFalse(meeting.ai_brief)
        self.assertTrue(meeting.summary_posted, "the note must still go out")
        self.assertIn("rollout plan", self._notes(self.target)[0].body)

    def test_an_mcp_arrival_enters_the_pipeline_too(self):
        """A meeting Sembly's automation never pushed is seen only by the MCP
        sync. Before this it could never be posted at all: its one route was
        the hourly sweep, which ships disabled."""
        meeting = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=780001,
                 started_at=fields.Datetime.to_string(
                     fields.Datetime.now() - timedelta(hours=2))),
            {'minutes': [{'text': "<p>Agreed the plan.</p>"}]})
        self.assertTrue(meeting.has_summary)
        self.assertTrue(meeting.ai_match_queued)

    def test_the_backfill_does_not_queue_the_whole_history(self):
        """The historical import upserts through _upsert_from_mcp as well, so
        without the recency guard a 2 000-meeting backfill would buy 2 000 LLM
        matches and post into years of old records."""
        old = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=780002,
                 started_at="2024-03-05 09:00", finished_at="2024-03-05 09:30"),
            {'minutes': [{'text': "<p>Old meeting.</p>"}]})
        self.assertTrue(old.has_summary)
        self.assertFalse(old.ai_match_queued,
                         "an old meeting must never enter the queue")

    def test_the_second_channel_does_not_queue_the_same_meeting_twice(self):
        """MCP and the webhook both see the same meeting; the second sighting
        must be a no-op, not a second note."""
        meeting = self.Meeting._upsert_from_webhook(
            dict(fixtures.WEBHOOK_NOTES, meeting_id=780003), 'notes')
        self.assertTrue(meeting.ai_match_queued)
        again = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=780003,
                 started_at=fields.Datetime.to_string(
                     fields.Datetime.now() - timedelta(hours=1))),
            {'minutes': [{'text': "<p>Same meeting, other channel.</p>"}]})
        self.assertEqual(again, meeting)
        self.assertEqual(
            self.env['sembly.meeting'].search_count(
                [('sembly_meeting_id', '=', '780003')]), 1)
