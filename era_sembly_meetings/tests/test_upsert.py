# -*- coding: utf-8 -*-
"""Upsert convergence: the two channels must land on ONE record, in either
order, and neither may blank what the other supplied."""
from odoo.tests import TransactionCase, tagged

from . import fixtures


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyUpsert(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']

    def test_mcp_creates_record_with_summary_and_items(self):
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)
        self.assertEqual(meeting.sembly_meeting_id, str(fixtures.MEETING_ID))
        self.assertEqual(meeting.name, "Acme ERP rollout - kickoff")
        self.assertEqual(meeting.duration_seconds, 2700)
        self.assertEqual(meeting.platform, 'Zoom')
        self.assertEqual(meeting.source, 'mcp')
        self.assertTrue(meeting.has_summary)
        self.assertIn("September", meeting.summary)
        # minutes[] renders one <h4> section per type
        self.assertIn("Project Meeting", meeting.summary)
        # MCP has no transcript at all — that is the whole reason the webhook exists.
        self.assertFalse(meeting.transcript)
        # 1 task + 1 each of decision/issue/risk/requirement/highlight/noteworthy
        self.assertEqual(len(meeting.item_ids), 7)
        self.assertEqual(len(meeting.item_ids.filtered(lambda i: i.item_type == 'task')), 1)
        self.assertEqual(
            meeting.item_ids.filtered(lambda i: i.item_type == 'decision').name,
            "Rollout starts in September")

    def test_mcp_builds_meeting_url_from_template(self):
        meeting = self.Meeting._upsert_from_mcp(fixtures.LIST_MEETINGS_META)
        self.assertEqual(meeting.meeting_url,
                         'https://webapp.sembly.ai/meeting/%d' % fixtures.MEETING_ID)
        # media_url mirrors it, so the chatter link keeps working if Sembly ever
        # exposes a real media URL.
        self.assertEqual(meeting.media_url, meeting.meeting_url)

    def test_duplicate_mcp_payload_updates_not_duplicates(self):
        first = self.Meeting._upsert_from_mcp(fixtures.LIST_MEETINGS_META)
        second = self.Meeting._upsert_from_mcp(fixtures.LIST_MEETINGS_META)
        self.assertEqual(first, second)
        self.assertEqual(self.Meeting.search_count(
            [('sembly_meeting_id', '=', str(fixtures.MEETING_ID))]), 1)

    def test_mcp_then_webhook_converges_and_adds_transcript(self):
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)
        summary_before = meeting.summary

        same = self.Meeting._upsert_from_webhook(
            fixtures.WEBHOOK_TRANSCRIPTION, 'transcription')
        self.assertEqual(same, meeting)
        self.assertTrue(same.has_transcript)
        self.assertIn("Welcome everyone", same.transcript)
        # The transcription webhook carries no notes; it must NOT blank the
        # summary MCP already supplied.
        self.assertEqual(same.summary, summary_before)
        self.assertEqual(same.participant_emails,
                         "yasser@era.net.sa\nsara@acme-test.com")
        self.assertEqual(same.meeting_url,
                         "https://webapp.sembly.ai/meeting/%d" % fixtures.MEETING_ID)

    def test_webhook_then_mcp_converges_and_keeps_transcript(self):
        meeting = self.Meeting._upsert_from_webhook(
            fixtures.WEBHOOK_TRANSCRIPTION, 'transcription')
        self.assertTrue(meeting.has_transcript)
        transcript_before = meeting.transcript

        same = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)
        self.assertEqual(same, meeting)
        # The MCP re-sync must never blank the webhook's transcript.
        self.assertEqual(same.transcript, transcript_before)
        self.assertTrue(same.has_summary)
        self.assertEqual(len(same.item_ids), 7)
        self.assertEqual(self.Meeting.search_count(
            [('sembly_meeting_id', '=', str(fixtures.MEETING_ID))]), 1)

    def test_notes_webhook_fills_summary(self):
        meeting = self.Meeting._upsert_from_webhook(fixtures.WEBHOOK_NOTES, 'notes')
        self.assertTrue(meeting.has_summary)
        self.assertIn("September", meeting.summary)

    def test_task_webhook_creates_item_once(self):
        self.Meeting._upsert_from_webhook(fixtures.WEBHOOK_TRANSCRIPTION, 'transcription')
        meeting = self.Meeting._upsert_from_webhook(fixtures.WEBHOOK_TASK, 'task')
        tasks = meeting.item_ids.filtered(lambda i: i.item_type == 'task')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks.name, "Prepare the migration plan")
        self.assertEqual(tasks.assigned_to, "Sara Mansour")
        # Replayed by Sembly: keyed on item_id, so it updates rather than duplicates.
        self.Meeting._upsert_from_webhook(fixtures.WEBHOOK_TASK, 'task')
        self.assertEqual(
            len(meeting.item_ids.filtered(lambda i: i.item_type == 'task')), 1)

    def test_resync_replaces_mcp_items(self):
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)
        self.assertEqual(len(meeting.item_ids), 7)
        trimmed = dict(fixtures.GET_MEETING_DETAILS, decisions=[], risks=[])
        self.Meeting._upsert_from_mcp(fixtures.LIST_MEETINGS_META, trimmed)
        self.assertEqual(len(meeting.item_ids), 5)

    def test_datetime_parsing_both_formats(self):
        """MCP sends 'YYYY-MM-DD HH:MM'; the webhook sends ISO."""
        from odoo import fields
        mcp = self.Meeting._parse_dt("2026-08-10 09:00")
        hook = self.Meeting._parse_dt("2026-08-10T09:00:00")
        tzed = self.Meeting._parse_dt("2026-08-10T12:00:00+03:00")
        self.assertEqual(fields.Datetime.to_string(mcp), "2026-08-10 09:00:00")
        self.assertEqual(fields.Datetime.to_string(hook), "2026-08-10 09:00:00")
        # An offset is normalised to UTC, not truncated.
        self.assertEqual(fields.Datetime.to_string(tzed), "2026-08-10 09:00:00")
        self.assertFalse(self.Meeting._parse_dt(""))
        self.assertFalse(self.Meeting._parse_dt("garbage"))

    def test_summary_that_is_already_html_is_not_escaped(self):
        """Sembly's real minutes arrive as markup, not plain text. Escaping it
        makes the user read '<p><h2><b>✨ Summary</b>' instead of the summary."""
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META,
            {'minutes': [{'type': "GENERIC", 'text': fixtures.HTML_SUMMARY}]})
        self.assertNotIn('&lt;p&gt;', meeting.summary)
        self.assertNotIn('&lt;h2&gt;', meeting.summary)
        # The markup survives as markup...
        self.assertIn('<b>', meeting.summary)
        # ...and the Arabic body is intact.
        self.assertIn("راجع فريق المبيعات", meeting.summary)

    def test_plain_text_summary_still_gets_its_line_breaks(self):
        """The other half of the coercion: a genuinely plain note must not lose
        its structure, and a bare '<' must not be mistaken for markup."""
        plain = "First line\nSecond line\nrevenue < 100k"
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, {'minutes': [{'text': plain}]})
        self.assertIn('<br', meeting.summary)
        self.assertIn('&lt; 100k', meeting.summary)

    def test_notes_webhook_html_is_not_escaped_either(self):
        meeting = self.Meeting._upsert_from_webhook(
            dict(fixtures.WEBHOOK_NOTES, meeting_notes=fixtures.HTML_SUMMARY), 'notes')
        self.assertNotIn('&lt;p&gt;', meeting.summary)
        self.assertIn("راجع فريق المبيعات", meeting.summary)

    def test_summary_is_still_sanitized(self):
        """Coercing HTML must not mean trusting it."""
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META,
            {'minutes': [{'text': '<p>ok</p><script>alert(1)</script>'}]})
        self.assertIn('ok', meeting.summary)
        self.assertNotIn('<script', meeting.summary)

    def test_datetime_parsing_strips_the_timezone_label(self):
        """The live MCP server appends a label: '2026-08-10 12:30 (UTC)'.
        Without stripping it the date is dropped and the meeting lands with no
        started_at, which also removes it from the chatter window."""
        from odoo import fields
        parsed = self.Meeting._parse_dt(fixtures.LIVE_STARTED_AT)
        self.assertEqual(fields.Datetime.to_string(parsed), "2026-08-10 12:30:00")
        self.assertEqual(
            fields.Datetime.to_string(self.Meeting._parse_dt("2026-08-10 12:55 (GMT)")),
            "2026-08-10 12:55:00")

    def test_upsert_keeps_the_date_from_a_live_payload(self):
        from odoo import fields
        meeting = self.Meeting._upsert_from_mcp(dict(
            fixtures.LIST_MEETINGS_META,
            started_at=fixtures.LIVE_STARTED_AT,
            finished_at=fixtures.LIVE_FINISHED_AT))
        self.assertTrue(meeting.started_at, "the live '(UTC)' suffix must not drop the date")
        self.assertEqual(fields.Datetime.to_string(meeting.started_at),
                         "2026-08-10 12:30:00")
        self.assertEqual(fields.Datetime.to_string(meeting.finished_at),
                         "2026-08-10 12:55:00")

    def test_payload_without_meeting_id_is_ignored(self):
        self.assertFalse(self.Meeting._upsert_from_mcp({'title': 'x'}))
        self.assertFalse(self.Meeting._upsert_from_webhook({'meeting_title': 'x'}, 'notes'))

    def test_participants_resolved_to_partners(self):
        partner = self.env['res.partner'].create({
            'name': "Sara Mansour", 'email': "sara@acme-test.com"})
        meeting = self.Meeting._upsert_from_webhook(
            fixtures.WEBHOOK_TRANSCRIPTION, 'transcription')
        self.assertIn(partner, meeting.partner_ids)

    # ------------------------------------------------------------------ share
    def test_guest_link_is_built_from_a_uuid(self):
        """Sembly's guest link is base64 of a per-meeting UUID — verified
        against a real one: .../guest-access/meeting/YTI1ZDE0OWYt… decodes to
        a25d149f-fdf8-40b7-90e2-7a22808578fe."""
        built = self.Meeting._build_share_url("a25d149f-fdf8-40b7-90e2-7a22808578fe")
        self.assertEqual(
            built,
            "https://webapp.sembly.ai/guest-access/meeting/"
            "YTI1ZDE0OWYtZmRmOC00MGI3LTkwZTItN2EyMjgwODU3OGZl")

    def test_a_non_uuid_never_becomes_a_guest_link(self):
        """Guessing here would hand someone a link that 404s."""
        for value in ("14456479", "", None, "not-a-uuid", "a25d149f"):
            self.assertFalse(self.Meeting._build_share_url(value), value)

    def test_guest_link_cannot_be_derived_from_the_numeric_id(self):
        """The whole reason share_url is a stored field: MCP gives us the id,
        and the id says nothing about the UUID."""
        meeting = self.Meeting._upsert_from_mcp(fixtures.LIST_MEETINGS_META)
        self.assertFalse(meeting.share_url)
        self.assertTrue(meeting.meeting_url)

    def test_webhook_guest_link_is_stored_and_preferred(self):
        share = ("https://webapp.sembly.ai/guest-access/meeting/"
                 "YTI1ZDE0OWYtZmRmOC00MGI3LTkwZTItN2EyMjgwODU3OGZl")
        meeting = self.Meeting._upsert_from_webhook(
            dict(fixtures.WEBHOOK_NOTES, meeting_link=share), 'notes')
        self.assertEqual(meeting.share_url, share)
        # It must NOT be mistaken for the workspace link...
        self.assertNotEqual(meeting.meeting_url, share)
        # ...and it is what the button and the chatter note use.
        self.assertEqual(meeting.media_url, share)
        self.assertEqual(meeting.action_open_sembly()['url'], share)

    def test_webhook_uuid_builds_the_guest_link(self):
        meeting = self.Meeting._upsert_from_webhook(
            dict(fixtures.WEBHOOK_NOTES,
                 meeting_uuid="a25d149f-fdf8-40b7-90e2-7a22808578fe"), 'notes')
        self.assertIn('guest-access', meeting.share_url or '')

    def test_without_a_guest_link_the_workspace_link_is_used(self):
        meeting = self.Meeting._upsert_from_mcp(fixtures.LIST_MEETINGS_META)
        self.assertEqual(meeting.action_open_sembly()['url'], meeting.meeting_url)

    def test_webhook_payload_shape_is_kept_for_diagnosis(self):
        """Sembly's hook selection and field names are configurable and it
        publishes neither, so the envelope is the only way to answer 'what did
        they actually send' — the question that stalled the guest-link work."""
        import json
        meeting = self.Meeting._upsert_from_webhook(
            fixtures.WEBHOOK_TRANSCRIPTION, 'transcription')
        stored = json.loads(meeting.raw_payload)
        self.assertIn('meeting_link', stored)
        self.assertIn('participants', stored)
        # The bulk is replaced by a marker, not duplicated: it already lives in
        # its own column.
        self.assertNotIn("Welcome everyone", meeting.raw_payload)
        self.assertTrue(stored['meeting_transcription'].endswith('chars>'))
        # ...while the real transcript is still stored properly.
        self.assertIn("Welcome everyone", meeting.transcript)

    def test_a_missing_link_field_is_visible_in_the_kept_payload(self):
        """The exact case we could not resolve: meeting_link absent vs present
        with the same value our template builds."""
        import json
        payload = {k: v for k, v in fixtures.WEBHOOK_NOTES.items()
                   if k != 'meeting_link'}
        meeting = self.Meeting._upsert_from_webhook(payload, 'notes')
        self.assertNotIn('meeting_link', json.loads(meeting.raw_payload))
