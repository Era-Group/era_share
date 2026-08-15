# -*- coding: utf-8 -*-
import io
import os
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from ..models.res_config_settings import API_KEY_PARAM
from ..services.assemblyai_client import AssemblyAIClient
from odoo.addons.era_sembly_meetings_google.services.google_workspace_client import (
    GoogleWorkspaceClient)


class _Response:
    def __init__(self, data, status=200, headers=None):
        self.data = data
        self.status_code = status
        self.headers = headers or {}
        self.text = ''
        self.reason = ''

    def json(self):
        return self.data


class _Session:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class _AssemblyClient:
    def __init__(self, result=None, found=None):
        self.result = result
        self.found = found
        self.deleted = []
        self.submit_calls = 0

    def get_transcript(self, transcript_id):
        return self.result

    def delete_transcript(self, transcript_id):
        self.deleted.append(transcript_id)
        return True

    def upload_file(self, stream):
        self.uploaded = stream.read()
        return 'https://cdn.example/private-upload'

    def submit(self, upload_url):
        self.submit_calls += 1
        self.submitted = upload_url
        return 'private-transcript-id'

    def find_transcript(self, upload_url, created_on=None):
        self.searched = upload_url
        self.searched_dates = created_on
        return self.found


class _GoogleClient:
    def __init__(self):
        self.shared = False

    def download_file_to(self, file_id, destination, max_bytes=None):
        destination.write(b'private-google-mp4')
        destination.seek(0)
        self.downloaded = file_id

    def share_anyone_with_link(self, file_id):
        self.shared = True
        raise AssertionError("Private transcription must never publish Drive files")


class _DownloadResponse:
    status_code = 200
    text = ''

    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def iter_content(self, _size):
        return iter(self.chunks)

    def close(self):
        self.closed = True


@tagged('post_install', '-at_install', 'sembly')
class TestAssemblyAI(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Meeting = cls.env['sembly.meeting']
        cls.icp = cls.env['ir.config_parameter'].sudo()
        cls.icp.set_param('sembly.assemblyai_enabled', '1')
        cls.icp.set_param(API_KEY_PARAM, 'test-assembly-key')
        cls.icp.set_param('sembly.assemblyai_wait_hours', '2')
        cls.icp.set_param('sembly.assemblyai_monthly_hours', '150')

    def _meeting(self, suffix, age_hours=1, transcript=False):
        return self.Meeting.sudo().with_context(sembly_sync=True).create({
            'sembly_meeting_id': 'google:file-%s' % suffix,
            'name': 'Google meeting %s' % suffix,
            'started_at': fields.Datetime.now() - timedelta(hours=age_hours),
            'duration_seconds': 3600,
            'source': 'google',
            'google_file_id': 'file-%s' % suffix,
            'google_owner_email': 'owner@example.com',
            'transcript': transcript or False,
        })

    def test_google_only_starts_immediately_but_hybrid_waits_two_hours(self):
        with patch.dict(os.environ, {'SEMBLY_MCP_TOKEN': ''}):
            self.icp.set_param('sembly.mcp_token', '')
            self.icp.set_param('sembly.assemblyai_sembly_policy', 'auto')
            immediate = self._meeting('immediate')
            immediate._assemblyai_maybe_queue()
            self.assertEqual(immediate.assemblyai_state, 'queued')

            self.icp.set_param('sembly.assemblyai_sembly_policy', 'always')
            waiting = self._meeting('waiting')
            waiting._assemblyai_maybe_queue()
            self.assertEqual(waiting.assemblyai_state, 'waiting_sembly')
            self.assertGreaterEqual(
                waiting.assemblyai_due_at,
                waiting.assemblyai_imported_at + timedelta(hours=2))

    def test_recordings_older_than_two_calendar_days_never_enter_the_queue(self):
        meeting = self._meeting('old', age_hours=73)
        meeting._assemblyai_maybe_queue()
        self.assertEqual(meeting.assemblyai_state, 'too_old')
        self.assertFalse(meeting.assemblyai_transcript_id)

    def test_manual_request_bypasses_calendar_age_limit(self):
        meeting = self._meeting('old-manual', age_hours=240)
        meeting.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'too_old'})
        self.assertTrue(meeting.assemblyai_can_request)
        meeting.action_assemblyai_request_now()
        self.assertTrue(meeting.assemblyai_manual_request)
        google = _GoogleClient()
        assembly = _AssemblyClient()
        with patch.object(type(self.Meeting), '_google_client', return_value=google), \
                patch.object(type(self.Meeting), '_assemblyai_client',
                             return_value=assembly), \
                patch('odoo.addons.era_sembly_meetings_assemblyai.models.'
                      'sembly_meeting.prepare_audio', side_effect=lambda source, _dir: source):
            self.assertTrue(meeting._assemblyai_submit_recording())

    def test_recordings_shorter_than_ten_minutes_never_enter_the_queue(self):
        self.icp.set_param('sembly.assemblyai_sembly_policy', 'never')
        short = self._meeting('short')
        short.sudo().with_context(sembly_sync=True).write({
            'duration_seconds': 599})
        short._assemblyai_maybe_queue()
        self.assertEqual(short.assemblyai_state, 'too_short')
        self.assertFalse(short.assemblyai_transcript_id)

        boundary = self._meeting('ten-minutes')
        boundary.sudo().with_context(sembly_sync=True).write({
            'duration_seconds': 600})
        boundary._assemblyai_maybe_queue()
        self.assertEqual(boundary.assemblyai_state, 'queued')

    def test_short_recording_is_rechecked_before_any_network_call(self):
        meeting = self._meeting('short-before-submit')
        meeting.sudo().with_context(sembly_sync=True).write({
            'duration_seconds': 300, 'assemblyai_state': 'queued'})
        with patch.object(type(self.Meeting), '_google_client') as google:
            self.assertFalse(meeting._assemblyai_submit_recording())
        google.assert_not_called()
        self.assertEqual(meeting.assemblyai_state, 'too_short')

    def test_manager_can_bypass_sembly_wait_from_transcript_tab(self):
        meeting = self._meeting('request-now')
        meeting.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'waiting_sembly',
            'assemblyai_due_at': fields.Datetime.now() + timedelta(hours=2),
        })
        self.assertTrue(meeting.assemblyai_can_request)
        action = meeting.action_assemblyai_request_now()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'success')
        self.assertEqual(meeting.assemblyai_state, 'queued')
        self.assertLessEqual(meeting.assemblyai_due_at, fields.Datetime.now())

    def test_regular_internal_employee_cannot_request_paid_transcription(self):
        employee = self.env['res.users'].create({
            'name': 'AssemblyAI button employee',
            'login': 'assemblyai_button_employee_test',
            'group_ids': [(4, self.env.ref('base.group_user').id)],
        })
        meeting = self._meeting('employee-request')
        with self.assertRaises(AccessError):
            meeting.with_user(employee).action_assemblyai_request_now()

    def test_project_manager_can_use_request_button(self):
        project_manager = self.env['res.users'].create({
            'name': 'AssemblyAI project manager',
            'login': 'assemblyai_project_manager_test',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('project.group_project_manager').id),
            ],
        })
        meeting = self._meeting('project-manager-request')
        action = meeting.with_user(
            project_manager).action_assemblyai_request_now()
        self.assertEqual(action['params']['type'], 'success')
        self.assertEqual(meeting.assemblyai_state, 'queued')

    def test_manual_request_bypasses_short_limit_but_not_active_request(self):
        short = self._meeting('request-short')
        short.sudo().with_context(sembly_sync=True).write({
            'duration_seconds': 599,
            'assemblyai_state': 'too_short',
        })
        self.assertTrue(short.assemblyai_can_request)
        short.action_assemblyai_request_now()
        self.assertEqual(short.assemblyai_state, 'queued')
        self.assertTrue(short.assemblyai_manual_request)
        google = _GoogleClient()
        assembly = _AssemblyClient()
        with patch.object(type(self.Meeting), '_google_client', return_value=google), \
                patch.object(type(self.Meeting), '_assemblyai_client',
                             return_value=assembly), \
                patch('odoo.addons.era_sembly_meetings_assemblyai.models.'
                      'sembly_meeting.prepare_audio', side_effect=lambda source, _dir: source):
            self.assertTrue(short._assemblyai_submit_recording())

        active = self._meeting('request-active')
        active.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'processing',
            'assemblyai_transcript_id': 'active-request-id',
        })
        self.assertFalse(active.assemblyai_can_request)
        action = active.action_assemblyai_request_now()
        self.assertEqual(action['params']['type'], 'info')

    def test_sembly_wait_cannot_be_shortened_below_two_hours(self):
        self.icp.set_param('sembly.assemblyai_sembly_policy', 'always')
        self.icp.set_param('sembly.assemblyai_wait_hours', '0')
        meeting = self._meeting('minimum-wait')
        meeting._assemblyai_maybe_queue()
        self.assertEqual(meeting.assemblyai_state, 'waiting_sembly')
        self.assertGreaterEqual(
            meeting.assemblyai_due_at,
            meeting.assemblyai_imported_at + timedelta(hours=2))

    def test_existing_transcript_prevents_queueing(self):
        meeting = self._meeting('has-text', transcript='Sembly transcript')
        meeting._assemblyai_maybe_queue()
        self.assertFalse(meeting.assemblyai_state)

    def test_completed_utterances_are_formatted_and_remote_data_deleted(self):
        meeting = self._meeting('poll')
        meeting.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'processing',
            'assemblyai_transcript_id': 'remote-1',
            'assemblyai_requested_at': fields.Datetime.now(),
        })
        client = _AssemblyClient({
            'status': 'completed',
            'speech_model_used': 'universal-2',
            'language_code': 'ar',
            'audio_duration': 120,
            'utterances': [
                {'speaker': 'A', 'start': 12000, 'text': 'السلام عليكم'},
                {'speaker': 'B', 'start': 18000, 'text': 'وعليكم السلام'},
            ],
        })
        with patch.object(type(self.Meeting), '_assemblyai_client',
                          return_value=client), \
                patch.object(type(self.Meeting), '_ask_agent', return_value=(
                    '{"arabic_html": "<h4>الملخص</h4><p>تم بدء الاجتماع.</p>"}'
                )) as ask:
            meeting._assemblyai_poll()
        self.assertEqual(meeting.assemblyai_state, 'completed')
        self.assertEqual(meeting.assemblyai_provider, 'assemblyai')
        self.assertIn('[00:00:12] المتحدث A', meeting.transcript)
        self.assertEqual(meeting.assemblyai_speaker_count, 2)
        self.assertTrue(meeting.assemblyai_remote_deleted)
        self.assertEqual(client.deleted, ['remote-1'])
        ask.assert_called_once()
        self.assertEqual(meeting.assemblyai_summary_source, 'generated')
        self.assertEqual(meeting.assemblyai_summary_state, 'completed')
        self.assertIn('تم بدء الاجتماع', meeting.final_summary)
        self.assertEqual(meeting.final_summary_label, 'AssemblyAI')

    def test_transcript_is_summarized_and_merged_with_existing_sembly_summary(self):
        meeting = self._meeting('summary-merge')
        meeting.sudo().with_context(sembly_sync=True).write({
            'summary': '<p>Sembly: budget approved and Sara owns delivery.</p>',
            'transcript': 'المتحدث A: تمت الموافقة على الميزانية. '
                          'المتحدث B: سارة مسؤولة عن التسليم.',
        })
        answer = ('{"arabic_html": "<h4>القرارات</h4>'
                  '<p>اعتمدت الميزانية وتتولى سارة التسليم.</p>"}')
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value=answer) as ask:
            self.assertTrue(meeting._assemblyai_generate_summary())
        prompt = ask.call_args.args[0]
        self.assertIn('ملخص Sembly', prompt)
        self.assertIn('تفريغ AssemblyAI', prompt)
        self.assertEqual(meeting.assemblyai_summary_source, 'merged')
        self.assertIn('سارة', meeting.final_summary)
        self.assertEqual(
            meeting.final_summary_label, 'Sembly + AssemblyAI (مدموج)')
        self.assertIn('Sembly: budget approved', meeting.summary)

    def test_late_sembly_summary_upgrades_generated_summary_into_a_merge(self):
        meeting = self._meeting(
            'late-summary', transcript='تفريغ AssemblyAI الأصلي')
        meeting.sudo().with_context(sembly_sync=True).write({
            'assemblyai_summary': '<p>ملخص AssemblyAI الأول.</p>',
            'assemblyai_summary_source': 'generated',
            'assemblyai_summary_state': 'completed',
            # Simulate Sembly later replacing the visible transcript as well.
            'summary': '<p>ملخص Sembly المتأخر.</p>',
            'transcript': 'تفريغ Sembly المتأخر',
        })
        meeting._assemblyai_queue_summary_merge()
        self.assertEqual(meeting.assemblyai_summary_state, 'pending')
        answer = '{"arabic_html": "<p>النسخة المدمجة النهائية.</p>"}'
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value=answer) as ask:
            meeting._assemblyai_generate_summary()
        prompt = ask.call_args.args[0]
        self.assertIn('ملخص AssemblyAI الأول', prompt)
        self.assertNotIn('تفريغ Sembly المتأخر', prompt)
        self.assertEqual(meeting.assemblyai_summary_source, 'merged')

    def test_summary_failure_keeps_successful_transcript_and_retries_later(self):
        meeting = self._meeting(
            'summary-failure', transcript='تفريغ ناجح يجب ألا يحذف')
        with patch.object(type(self.Meeting), '_ask_agent',
                          side_effect=ValueError('agent unavailable')):
            self.assertFalse(meeting._assemblyai_generate_summary())
        self.assertEqual(meeting.transcript, 'تفريغ ناجح يجب ألا يحذف')
        self.assertEqual(meeting.assemblyai_summary_state, 'failed')
        self.assertEqual(meeting.assemblyai_summary_attempts, 1)
        self.assertTrue(meeting.assemblyai_summary_next_retry_at)

    def test_sembly_webhook_wins_and_queues_remote_deletion(self):
        meeting = self.Meeting.sudo().with_context(sembly_sync=True).create({
            'sembly_meeting_id': '991001',
            'name': 'Hybrid meeting',
            'started_at': fields.Datetime.now() - timedelta(hours=1),
            'source': 'mcp',
            'google_file_id': 'hybrid-file',
            'assemblyai_state': 'processing',
            'assemblyai_transcript_id': 'remote-hybrid',
        })
        same = self.Meeting._upsert_from_webhook({
            'meeting_id': '991001',
            'meeting_title': 'Hybrid meeting',
            'meeting_transcription': 'Authoritative Sembly text',
        }, 'transcription')
        self.assertEqual(same, meeting)
        self.assertEqual(meeting.transcript, 'Authoritative Sembly text')
        self.assertEqual(meeting.assemblyai_provider, 'sembly')
        self.assertEqual(meeting.assemblyai_state, 'cancel_pending')

    def test_monthly_budget_blocks_submission_before_any_network_call(self):
        prior = self._meeting('prior')
        prior.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'processing',
            'assemblyai_transcript_id': 'prior-remote',
            'assemblyai_requested_at': fields.Datetime.now(),
            'assemblyai_audio_seconds': 3600,
        })
        self.icp.set_param('sembly.assemblyai_monthly_hours', '1')
        candidate = self._meeting('budget')
        with patch.object(type(self.Meeting), '_google_client') as google:
            self.assertFalse(candidate._assemblyai_submit_recording())
        google.assert_not_called()
        self.assertEqual(candidate.assemblyai_state, 'budget_blocked')

    def test_submission_downloads_privately_and_never_shares_drive_file(self):
        meeting = self._meeting('private')
        google = _GoogleClient()
        assembly = _AssemblyClient()
        with patch.object(type(self.Meeting), '_google_client', return_value=google), \
                patch.object(type(self.Meeting), '_assemblyai_client',
                             return_value=assembly), \
                patch('odoo.addons.era_sembly_meetings_assemblyai.models.'
                      'sembly_meeting.prepare_audio', side_effect=lambda source, _dir: source):
            self.assertTrue(meeting._assemblyai_submit_recording())
        self.assertEqual(google.downloaded, meeting.google_file_id)
        self.assertFalse(google.shared)
        self.assertEqual(assembly.uploaded, b'private-google-mp4')
        self.assertEqual(meeting.assemblyai_state, 'processing')
        self.assertFalse(meeting.google_share_url)

    def test_google_orphan_adoption_carries_an_inflight_remote_job(self):
        orphan = self._meeting('adoption')
        orphan.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'processing',
            'assemblyai_transcript_id': 'remote-adoption',
            'assemblyai_requested_at': fields.Datetime.now(),
        })
        target = self.Meeting.sudo().with_context(sembly_sync=True).create({
            'sembly_meeting_id': '991002',
            'name': orphan.name,
            'started_at': orphan.started_at,
            'source': 'mcp',
        })
        self.assertTrue(self.Meeting._adopt_orphan_google_record(target))
        self.assertFalse(orphan.exists())
        self.assertEqual(target.google_file_id, 'file-adoption')
        self.assertEqual(target.assemblyai_state, 'processing')
        self.assertEqual(target.assemblyai_transcript_id, 'remote-adoption')

    def test_lost_submit_response_is_recovered_without_second_submit(self):
        meeting = self._meeting('recover')
        meeting.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'submitting',
            'assemblyai_upload_url': 'https://cdn.example/recover',
            'assemblyai_region': 'eu',
            'assemblyai_submitting_at': fields.Datetime.now() - timedelta(minutes=11),
            'assemblyai_requested_at': fields.Datetime.now(),
            'assemblyai_audio_seconds': 3600,
        })
        client = _AssemblyClient(found={
            'id': 'recovered-remote-id', 'status': 'processing'})
        with patch.object(type(self.Meeting), '_assemblyai_client',
                          return_value=client) as factory:
            meeting._assemblyai_recover_submit()
        factory.assert_called_once_with(region='eu')
        self.assertEqual(meeting.assemblyai_transcript_id, 'recovered-remote-id')
        self.assertEqual(meeting.assemblyai_state, 'processing')
        self.assertEqual(client.submit_calls, 0)

    def test_submit_recovery_never_resubmits_after_the_48_hour_limit(self):
        meeting = self._meeting('recover-old', age_hours=47)
        meeting.sudo().with_context(sembly_sync=True).write({
            'started_at': fields.Datetime.now() - timedelta(hours=73),
            'assemblyai_state': 'submitting',
            'assemblyai_upload_url': 'https://cdn.example/old',
            'assemblyai_region': 'us',
            'assemblyai_submitting_at': fields.Datetime.now() - timedelta(minutes=11),
            'assemblyai_requested_at': fields.Datetime.now(),
            'assemblyai_audio_seconds': 3600,
        })
        client = _AssemblyClient(found=None)
        with patch.object(type(self.Meeting), '_assemblyai_client',
                          return_value=client):
            meeting._assemblyai_recover_submit()
        self.assertEqual(meeting.assemblyai_state, 'too_old')
        self.assertEqual(client.submit_calls, 0)

    def test_disabling_new_submissions_still_deletes_remote_data(self):
        meeting = self._meeting('disabled-cleanup')
        meeting.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'completed',
            'assemblyai_transcript_id': 'delete-while-disabled',
            'assemblyai_region': 'us',
            'assemblyai_requested_at': fields.Datetime.now(),
        })
        self.icp.set_param('sembly.assemblyai_enabled', '0')
        client = _AssemblyClient()
        with patch.object(type(self.Meeting), '_assemblyai_client',
                          return_value=client):
            self.Meeting._cron_assemblyai_transcriptions()
        self.assertTrue(meeting.assemblyai_remote_deleted)
        self.assertEqual(client.deleted, ['delete-while-disabled'])

    def test_api_key_is_write_only_and_mask_preserves_it(self):
        secret = 'assembly-super-secret-value'
        self.icp.set_param(API_KEY_PARAM, secret)
        Settings = self.env['res.config.settings']
        values = Settings.get_values()
        self.assertNotEqual(values['sembly_assemblyai_api_key'], secret)
        self.assertTrue(values['sembly_assemblyai_api_key'].startswith('•'))
        wizard = Settings.create({
            'sembly_assemblyai_api_key': values['sembly_assemblyai_api_key']})
        wizard.set_values()
        self.assertEqual(self.icp.get_param(API_KEY_PARAM), secret)

    def test_rest_contract_uses_raw_key_raw_upload_and_explicit_u2(self):
        session = _Session([
            _Response({'upload_url': 'https://cdn.example/private'}),
            _Response({'id': 'transcript-1', 'status': 'queued'}),
        ])
        client = AssemblyAIClient('raw-key', session=session)
        upload_url = client.upload_file(io.BytesIO(b'private-media'))
        transcript_id = client.submit(upload_url)
        self.assertEqual(session.headers['Authorization'], 'raw-key')
        upload = session.calls[0]
        self.assertEqual(upload[0], 'POST')
        self.assertEqual(upload[2]['headers']['Content-Type'],
                         'application/octet-stream')
        self.assertNotIn('files', upload[2])
        payload = session.calls[1][2]['json']
        self.assertEqual(payload['speech_models'], ['universal-2'])
        self.assertEqual(payload['language_code'], 'ar')
        self.assertIs(payload['speaker_labels'], True)
        self.assertEqual(transcript_id, 'transcript-1')

    def test_google_blob_download_uses_oauth_without_public_permissions(self):
        client = GoogleWorkspaceClient({}, subject='owner@example.com')
        response = _DownloadResponse([b'private-', b'video'])
        metadata = {
            'mimeType': 'video/mp4', 'size': '13',
            'capabilities': {'canDownload': True},
        }
        destination = io.BytesIO()
        with patch.object(client, '_call', return_value=metadata) as api_call, \
                patch.object(client, '_access_token', return_value='google-token'), \
                patch('odoo.addons.era_sembly_meetings_google.services.'
                      'google_workspace_client.requests.get', return_value=response) as get:
            client.download_file_to('private-file', destination, max_bytes=100)
        self.assertEqual(destination.read(), b'private-video')
        self.assertEqual(get.call_args.kwargs['params']['alt'], 'media')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'],
                         'Bearer google-token')
        self.assertNotIn('permissions', get.call_args.args[0])
        self.assertNotIn('permissions', api_call.call_args.args[1])
        self.assertTrue(response.closed)

    def test_submit_recovery_paginates_until_the_private_url_is_found(self):
        first_page = [
            {'id': 'id-%s' % index, 'audio_url': 'https://cdn.example/%s' % index}
            for index in range(200)]
        session = _Session([
            _Response({'transcripts': first_page, 'page_details': {}}),
            _Response({'transcripts': [{
                'id': 'wanted-id', 'audio_url': 'https://cdn.example/wanted'}],
                'page_details': {}}),
        ])
        client = AssemblyAIClient('raw-key', session=session)
        found = client.find_transcript(
            'https://cdn.example/wanted', created_on=['2026-08-15'])
        self.assertEqual(found['id'], 'wanted-id')
        self.assertEqual(session.calls[1][2]['params']['before_id'], 'id-199')
