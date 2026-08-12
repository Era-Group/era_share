# -*- coding: utf-8 -*-
"""Webhook controller — auth, limits and the transcript path.

HttpCase so the real route, the real token check and the real rate limiter run.
"""
import json

from odoo.tests import HttpCase, tagged

from ..controllers import sembly_webhook
from . import fixtures


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyWebhook(HttpCase):

    def setUp(self):
        super().setUp()
        self.token = self.env['ir.config_parameter'].sudo().get_param(
            'sembly.webhook_token')
        self.assertTrue(self.token, "post_init must have generated a webhook token")
        # The limiter is process-global; clear it so tests do not leak into
        # each other or into a real request.
        sembly_webhook._rate_buckets.clear()

    def _post(self, payload, token=None):
        return self.url_open(
            '/sembly/webhook/%s' % (token or self.token),
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json'})

    def test_get_returns_active(self):
        """Sembly's Test button and manual checks must succeed."""
        response = self.url_open('/sembly/webhook/%s' % self.token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('active', response.text)

    def test_wrong_token_is_forbidden(self):
        response = self._post(fixtures.WEBHOOK_TRANSCRIPTION, token='not-the-token')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.env['sembly.meeting'].search(
            [('sembly_meeting_id', '=', str(fixtures.MEETING_ID))]))

    def test_valid_transcription_creates_record(self):
        response = self._post(fixtures.WEBHOOK_TRANSCRIPTION)
        self.assertEqual(response.status_code, 200)
        meeting = self.env['sembly.meeting'].search(
            [('sembly_meeting_id', '=', str(fixtures.MEETING_ID))])
        self.assertEqual(len(meeting), 1)
        self.assertTrue(meeting.has_transcript)
        self.assertIn("Welcome everyone", meeting.transcript)
        self.assertEqual(meeting.source, 'webhook')
        # Matching is deferred to the cron so the response stays fast.
        self.assertEqual(meeting.link_state, 'unlinked')
        meeting.unlink()

    def test_replayed_payload_does_not_duplicate(self):
        self._post(fixtures.WEBHOOK_TRANSCRIPTION)
        self._post(fixtures.WEBHOOK_TRANSCRIPTION)
        self.assertEqual(self.env['sembly.meeting'].search_count(
            [('sembly_meeting_id', '=', str(fixtures.MEETING_ID))]), 1)
        self.env['sembly.meeting'].search(
            [('sembly_meeting_id', '=', str(fixtures.MEETING_ID))]).unlink()

    def test_oversized_body_rejected(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.webhook_max_bytes', '500')
        try:
            payload = dict(fixtures.WEBHOOK_TRANSCRIPTION,
                           meeting_transcription="x" * 5000)
            response = self._post(payload)
            self.assertEqual(response.status_code, 413)
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'sembly.webhook_max_bytes', '1048576')

    def test_malformed_json_rejected(self):
        response = self.url_open(
            '/sembly/webhook/%s' % self.token, data="{not json",
            headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 400)

    def test_rate_limit(self):
        """121 requests inside the window must be refused (rule 13)."""
        ip = '10.9.9.9'
        for _ in range(sembly_webhook.RATE_LIMIT_MAX):
            self.assertFalse(sembly_webhook._rate_limited(ip))
        self.assertTrue(sembly_webhook._rate_limited(ip))
        sembly_webhook._rate_buckets.clear()

    def test_discriminator(self):
        """Sembly's automations are told apart by field presence."""
        disc = sembly_webhook.SemblyWebhook._discriminate
        self.assertEqual(disc(fixtures.WEBHOOK_TRANSCRIPTION), 'transcription')
        self.assertEqual(disc(fixtures.WEBHOOK_NOTES), 'notes')
        self.assertEqual(disc(fixtures.WEBHOOK_TASK), 'task')

    def test_payload_without_meeting_id_is_logged_with_its_keys(self):
        """Sembly publishes no schema for these payloads, so a rejection has to
        say what the body actually looked like — otherwise a renamed field is
        indistinguishable from a test ping and stays a dead end."""
        response = self._post({'meeting_notes': "orphan", 'callId': 42})
        self.assertEqual(response.status_code, 200)
        self.assertIn('ignored', response.text)
        log = self.env['sembly.sync.log'].search(
            [('channel', '=', 'webhook'), ('state', '=', 'error')],
            order='id desc', limit=1)
        self.assertIn('callId', log.message)
        self.assertIn('meeting_notes', log.message)
        # The VALUES must never be logged: the body carries the transcript and
        # participant emails.
        self.assertNotIn('orphan', log.message)

    def test_a_renamed_id_field_is_still_accepted(self):
        meeting = self.env['sembly.meeting'].sudo()._upsert_from_webhook(
            {'meetingId': 5150, 'meeting_notes': "hello"}, 'notes')
        self.assertEqual(meeting.sembly_meeting_id, '5150')
        meeting.unlink()

    def test_any_sembly_supplied_link_wins_over_the_built_one(self):
        """MCP exposes no URL at all, so whatever link the webhook carries —
        workspace or guest/share — is the only real one and must win."""
        meeting = self.env['sembly.meeting'].sudo()._upsert_from_webhook(
            {'meeting_id': 5151, 'meeting_notes': "hi",
             'share_link': "https://webapp.sembly.ai/share/abc123"}, 'notes')
        self.assertEqual(meeting.meeting_url,
                         "https://webapp.sembly.ai/share/abc123")
        self.assertEqual(meeting.media_url, meeting.meeting_url)
        meeting.unlink()

    def test_sembly_test_payload_creates_no_meeting(self):
        """Sembly's Test button posts placeholder data (meeting_id 0,
        owner@sembly.ai). Taking it at face value created a junk meeting that
        polluted the list and cost an AI match — once per Test click."""
        before = self.env['sembly.meeting'].search_count([])
        response = self._post({
            'meeting_id': 0, 'workspace_id': 0,
            'meeting_owner_email': "owner@sembly.ai",
            'automation_owner_email': "owner@sembly.ai",
            'meeting_title': "Meeting Test Title",
            'meeting_link': "https://webapp.sembly.ai/meeting/0",
            'participants': ["test1@sembly.ai", "test2@sembly.ai"],
            'meeting_notes': "Test notes",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('test payload', response.text)
        self.assertEqual(self.env['sembly.meeting'].search_count([]), before)
        self.assertFalse(self.env['sembly.meeting'].search(
            [('sembly_meeting_id', '=', '0')]))
        # It is still logged, WITH its field names — that is how the real
        # payload shape was identified in the first place.
        log = self.env['sembly.sync.log'].search(
            [('channel', '=', 'webhook')], order='id desc', limit=1)
        self.assertEqual(log.state, 'ok')
        self.assertIn('meeting_link', log.message)

    def test_a_real_meeting_from_sembly_staff_is_not_mistaken_for_a_test(self):
        """The guard needs BOTH a zero id and a sembly.ai address, so a real
        meeting can never be swallowed."""
        meeting = self.env['sembly.meeting'].sudo()._upsert_from_webhook(
            {'meeting_id': 4242, 'meeting_owner_email': "owner@sembly.ai",
             'meeting_notes': "real"}, 'notes')
        self.assertTrue(meeting)
        self.assertEqual(meeting.sembly_meeting_id, '4242')
        meeting.unlink()
