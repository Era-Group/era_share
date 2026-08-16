# -*- coding: utf-8 -*-
"""Asynchronous AssemblyAI fallback for recent private Google recordings."""
import os
import shutil
import tempfile
import hashlib
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.assemblyai_client import AssemblyAIClient, AssemblyAIError
from ..services.media import ffmpeg_executable, prepare_audio


MAX_SOURCE_BYTES = 10 * 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 2_200_000_000
MIN_DURATION_SECONDS = 10 * 60
PRICE_PER_HOUR = 0.17
ACTIVE_REMOTE_STATES = ('processing', 'cancel_pending')
TERMINAL_STATES = (
    'completed', 'superseded', 'skipped_sembly', 'too_old', 'too_short', 'failed')


class SemblyMeeting(models.Model):
    _inherit = 'sembly.meeting'

    assemblyai_state = fields.Selection([
        ('waiting_sembly', "بانتظار Sembly"),
        ('queued', "بانتظار التفريغ"),
        ('uploading', "جاري رفع التسجيل"),
        ('submitting', "جاري إنشاء الطلب"),
        ('processing', "جاري التفريغ"),
        ('cancel_pending', "بانتظار إلغاء الطلب"),
        ('completed', "اكتمل"),
        ('superseded', "استُبدل بتفريغ Sembly"),
        ('skipped_sembly', "وصل تفريغ Sembly"),
        ('too_old', "التسجيل أقدم من يومين"),
        ('too_short', "التسجيل أقصر من 10 دقائق"),
        ('budget_blocked', "متوقف بسبب الميزانية"),
        ('failed', "فشل"),
        ('uncertain', "حالة الطلب غير مؤكدة"),
    ], string="حالة AssemblyAI", copy=False, index=True)
    assemblyai_transcript_id = fields.Char(
        string="معرّف طلب AssemblyAI", copy=False, index=True)
    assemblyai_upload_url = fields.Char(
        string="رابط الرفع الخاص", copy=False,
        help="Temporary private AssemblyAI URL used only to recover an uncertain submit.")
    assemblyai_region = fields.Selection(
        [('us', "US"), ('eu', "EU")], string="منطقة الطلب", copy=False)
    assemblyai_key_fingerprint = fields.Char(
        string="بصمة مشروع AssemblyAI", copy=False)
    assemblyai_submitting_at = fields.Datetime(
        string="بدأ إنشاء الطلب", copy=False, index=True)
    assemblyai_provider = fields.Selection(
        [('assemblyai', "AssemblyAI"), ('sembly', "Sembly")],
        string="مصدر التفريغ", copy=False)
    assemblyai_model = fields.Char(string="نموذج التفريغ", copy=False)
    assemblyai_language_code = fields.Char(string="لغة التفريغ", copy=False)
    assemblyai_imported_at = fields.Datetime(
        string="وقت وصول تسجيل Google", copy=False, index=True)
    assemblyai_due_at = fields.Datetime(
        string="موعد بدء التفريغ", copy=False, index=True)
    assemblyai_requested_at = fields.Datetime(
        string="وقت إرسال الطلب", copy=False, index=True)
    assemblyai_completed_at = fields.Datetime(
        string="وقت اكتمال التفريغ", copy=False)
    assemblyai_next_retry_at = fields.Datetime(
        string="إعادة المحاولة بعد", copy=False, index=True)
    assemblyai_attempts = fields.Integer(string="عدد المحاولات", copy=False)
    assemblyai_audio_seconds = fields.Integer(
        string="الثواني المفوترة", copy=False)
    assemblyai_audio_channels = fields.Integer(
        string="قنوات الصوت المرفوعة", copy=False)
    assemblyai_estimated_cost = fields.Float(
        string="التكلفة التقديرية ($)", digits=(10, 4), copy=False)
    assemblyai_speaker_count = fields.Integer(
        string="عدد المتحدثين", copy=False)
    assemblyai_error = fields.Char(string="آخر خطأ", copy=False)
    assemblyai_remote_deleted = fields.Boolean(
        string="حُذفت البيانات البعيدة", copy=False)
    assemblyai_manual_request = fields.Boolean(
        string="طلب تفريغ يدوي", copy=False,
        help="Allows an explicit manager request to bypass the automatic "
             "10-minute minimum while preserving all billing safeguards.")
    assemblyai_summary = fields.Html(
        string="ملخص AssemblyAI", sanitize=True, copy=False,
        help="Arabic summary generated from the transcript, optionally merged "
             "with Sembly's original summary.")
    assemblyai_summary_source = fields.Selection([
        ('generated', "من تفريغ AssemblyAI"),
        ('merged', "دمج Sembly + AssemblyAI"),
    ], string="مصدر ملخص AssemblyAI", copy=False)
    assemblyai_summary_state = fields.Selection([
        ('pending', "بانتظار التلخيص"),
        ('completed', "اكتمل التلخيص"),
        ('failed', "فشل التلخيص"),
    ], string="حالة تلخيص AssemblyAI", copy=False, index=True)
    assemblyai_summary_attempts = fields.Integer(
        string="محاولات التلخيص", copy=False)
    assemblyai_summary_next_retry_at = fields.Datetime(
        string="إعادة التلخيص بعد", copy=False, index=True)
    assemblyai_summary_error = fields.Char(
        string="خطأ تلخيص AssemblyAI", copy=False)
    assemblyai_can_request = fields.Boolean(
        string="يمكن طلب التفريغ الآن",
        compute='_compute_assemblyai_can_request')

    _assemblyai_transcript_id_unique = models.Constraint(
        'UNIQUE(assemblyai_transcript_id)',
        "This AssemblyAI transcript is already attached to another meeting.")

    @api.depends('google_file_id', 'sembly_meeting_id', 'source',
                 'assemblyai_state')
    def _compute_provider_summary(self):
        super()._compute_provider_summary()
        for record in self:
            if record.assemblyai_state in ('processing', 'completed', 'superseded'):
                names = [name.strip() for name in
                         (record.provider_summary or '').split('+') if name.strip()]
                if 'AssemblyAI' not in names:
                    names.append('AssemblyAI')
                record.provider_summary = ' + '.join(names)

    @api.depends('transcript', 'google_file_id', 'duration_seconds', 'started_at',
                 'assemblyai_state', 'assemblyai_transcript_id')
    def _compute_assemblyai_can_request(self):
        blocked = {
            'uploading', 'submitting', 'processing', 'cancel_pending',
            'completed', 'superseded', 'skipped_sembly',
            'uncertain',
        }
        for record in self:
            record.assemblyai_can_request = bool(
                not record.transcript
                and record.google_file_id
                and record.assemblyai_state not in blocked
                and not (record.assemblyai_transcript_id
                         and not record.assemblyai_remote_deleted))

    @api.depends('summary', 'transcript', 'gemini_notes', 'gemini_notes_ar',
                 'assemblyai_summary')
    def _compute_has_content(self):
        return super()._compute_has_content()

    @api.depends('summary', 'ai_brief', 'gemini_notes', 'gemini_notes_ar',
                 'merged_summary_source', 'assemblyai_summary',
                 'assemblyai_summary_source')
    def _compute_final_summary(self):
        return super()._compute_final_summary()

    def _narrative_html(self, label):
        self.ensure_one()
        if label.startswith('Sembly + AssemblyAI') or label == 'AssemblyAI':
            return self.assemblyai_summary or False
        return super()._narrative_html(label)

    def _narrative_sources(self):
        self.ensure_one()
        from odoo.tools import html2plaintext
        text = html2plaintext(self.assemblyai_summary or '').strip()
        if text:
            label = ('Sembly + AssemblyAI (مدموج)'
                     if self.assemblyai_summary_source == 'merged'
                     else 'AssemblyAI')
            # This is the final synthesis derived from the transcript. Like the
            # Gemini merge, it replaces its inputs rather than duplicating them.
            return [(label, text)]
        return super()._narrative_sources()

    def action_show_assemblyai_summary(self):
        self.ensure_one()
        return self._open_text_dialog(
            _("ملخص AssemblyAI"), self.assemblyai_summary,
            note=_("Generated from the transcript and merged with Sembly when available."))

    @api.model
    def _assemblyai_api_key(self):
        return ((os.environ.get('ASSEMBLYAI_API_KEY') or '').strip()
                or (self._icp('sembly.assemblyai_api_key') or '').strip())

    @api.model
    def _assemblyai_enabled(self):
        return self._icp('sembly.assemblyai_enabled', '0') in ('1', 'True', 'true') \
            and bool(self._assemblyai_api_key())

    @api.model
    def _assemblyai_client(self, region=None):
        return AssemblyAIClient(
            self._assemblyai_api_key(),
            region=region or self._icp('sembly.assemblyai_region', 'us') or 'us')

    @api.model
    def _assemblyai_expects_sembly(self):
        policy = self._icp('sembly.assemblyai_sembly_policy', 'auto') or 'auto'
        if policy == 'always':
            return True
        if policy == 'never':
            return False
        return bool((os.environ.get('SEMBLY_MCP_TOKEN') or '').strip()
                    or (self._icp('sembly.mcp_token') or '').strip())

    @api.model
    def _assemblyai_month_usage_seconds(self, exclude_ids=None):
        now = fields.Datetime.now()
        month_start = datetime(now.year, now.month, 1)
        domain = [
            ('assemblyai_requested_at', '>=', fields.Datetime.to_string(month_start)),
            ('assemblyai_state', 'in', [
                'uploading', 'submitting', 'processing', 'cancel_pending',
                'completed', 'superseded', 'uncertain']),
        ]
        if exclude_ids:
            domain.append(('id', 'not in', list(exclude_ids)))
        records = self.sudo().search(domain)
        return sum(record.assemblyai_audio_seconds or record.duration_seconds or 0
                   for record in records)

    @staticmethod
    def _assemblyai_is_too_old(started_at, now=None):
        """Keep the current date and the two preceding calendar dates."""
        if not started_at:
            return True
        now = now or fields.Datetime.now()
        return started_at.date() < (now.date() - timedelta(days=2))

    def _assemblyai_maybe_queue(self, imported_at=None):
        now = fields.Datetime.now()
        # Two hours is the minimum Sembly window agreed for this fallback.
        wait = max(2, self._icp_int('sembly.assemblyai_wait_hours', 2))
        for record in self:
            if not self._assemblyai_enabled() or not record.google_file_id:
                continue
            if record.transcript:
                continue
            if record.assemblyai_state in (
                    'waiting_sembly', 'queued', 'uploading', 'submitting', 'processing',
                    'cancel_pending', 'completed', 'superseded',
                    'skipped_sembly', 'too_old', 'too_short', 'uncertain'):
                continue
            if record.assemblyai_state == 'failed' \
                    and record.assemblyai_attempts >= 5:
                continue
            if self._assemblyai_is_too_old(record.started_at, now):
                record.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'too_old',
                    'assemblyai_error': _(
                        "Recording is older than two calendar days"),
                })
                continue
            if record.duration_seconds and \
                    record.duration_seconds < MIN_DURATION_SECONDS:
                record.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'too_short',
                    'assemblyai_error': _(
                        "Recording is shorter than 10 minutes"),
                })
                continue
            arrived = record.assemblyai_imported_at or imported_at or now
            due = arrived + timedelta(hours=wait) \
                if self._assemblyai_expects_sembly() else now
            record.sudo().with_context(sembly_sync=True).write({
                'assemblyai_imported_at': arrived,
                'assemblyai_due_at': due,
                'assemblyai_state': 'waiting_sembly' if due > now else 'queued',
                'assemblyai_error': False,
            })
        cron = self.env.ref(
            'era_sembly_meetings_assemblyai.cron_sembly_assemblyai_transcriptions',
            raise_if_not_found=False)
        if cron and self.filtered(lambda r: r.assemblyai_state == 'queued'):
            cron.sudo()._trigger()
        return True

    @api.model
    def _upsert_from_google(self, recording, owner_email):
        record = super()._upsert_from_google(recording, owner_email)
        if record:
            meta = recording.get('videoMediaMetadata') or {}
            try:
                seconds = int(int(meta.get('durationMillis') or 0) / 1000)
            except (TypeError, ValueError):
                seconds = 0
            if seconds and not record.duration_seconds:
                record.sudo().with_context(sembly_sync=True).write({
                    'duration_seconds': seconds})
            record._assemblyai_maybe_queue()
        return record

    @api.model
    def _upsert_from_webhook(self, payload, kind):
        record = super()._upsert_from_webhook(payload, kind)
        transcript = payload.get('meeting_transcription') if isinstance(payload, dict) else None
        if record and transcript and str(transcript).strip():
            values = {'assemblyai_provider': 'sembly'}
            if record.assemblyai_transcript_id and not record.assemblyai_remote_deleted:
                values['assemblyai_state'] = 'cancel_pending'
            elif record.assemblyai_state == 'submitting':
                # Its POST response may have been lost. Recovery searches by
                # upload URL and deletes any job it finds.
                values['assemblyai_state'] = 'submitting'
            elif record.assemblyai_state:
                values['assemblyai_state'] = (
                    'superseded' if record.assemblyai_state == 'completed'
                    else 'skipped_sembly')
            record.sudo().with_context(sembly_sync=True).write(values)
            record._assemblyai_queue_summary_merge()
        elif record:
            record._assemblyai_maybe_queue()
            record._assemblyai_queue_summary_merge()
        return record

    @api.model
    def _upsert_from_mcp(self, meta, details=None):
        record = super()._upsert_from_mcp(meta, details)
        if record:
            record._assemblyai_maybe_queue()
            record._assemblyai_queue_summary_merge()
        return record

    @api.model
    def _extra_google_adoption_values(self, orphan, meeting):
        values = super()._extra_google_adoption_values(orphan, meeting)
        names = [name for name in self._fields if name.startswith('assemblyai_')]
        for name in names:
            value = orphan[name]
            if value and not meeting[name]:
                values[name] = value.id if getattr(value, 'id', False) else value
        if orphan.assemblyai_provider == 'assemblyai' and orphan.transcript \
                and not meeting.transcript:
            values['transcript'] = orphan.transcript
        # The target is written before Google deletes the orphan. Release the
        # unique remote id first so both rows never hold it in the same flush.
        if values.get('assemblyai_transcript_id'):
            orphan.sudo().with_context(sembly_sync=True).write({
                'assemblyai_transcript_id': False})
        return values

    @staticmethod
    def _assemblyai_format_transcript(data):
        lines, speakers = [], set()
        for utterance in data.get('utterances') or []:
            text = str(utterance.get('text') or '').strip()
            if not text:
                continue
            speaker = str(utterance.get('speaker') or '?')
            speakers.add(speaker)
            milliseconds = int(utterance.get('start') or 0)
            total = milliseconds // 1000
            stamp = '%02d:%02d:%02d' % (
                total // 3600, (total % 3600) // 60, total % 60)
            lines.append('[%s] المتحدث %s:\n%s' % (stamp, speaker, text))
        text = '\n\n'.join(lines) or str(data.get('text') or '').strip()
        return text, len(speakers)

    def _assemblyai_fail(self, exc):
        self.ensure_one()
        attempts = self.assemblyai_attempts + 1
        if isinstance(exc, AssemblyAIError) and exc.uncertain:
            state, retry_at = 'uncertain', False
        else:
            state = 'failed'
            delays = [5, 15, 60, 240, 1440]
            retry_at = fields.Datetime.now() + timedelta(
                minutes=delays[min(attempts - 1, len(delays) - 1)])
        self.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': state,
            'assemblyai_attempts': attempts,
            'assemblyai_next_retry_at': retry_at,
            'assemblyai_error': str(exc)[:500],
        })
        self.env['sembly.sync.log']._log(
            'ai', 'assemblyai', 'error',
            "meeting %s: %s" % (self.sembly_meeting_id, str(exc)[:300]))

    def _assemblyai_queue_summary_merge(self):
        """Upgrade a transcript-only summary when Sembly arrives later."""
        for record in self:
            if record.transcript and record.assemblyai_summary \
                    and record.summary \
                    and record.assemblyai_summary_source != 'merged':
                record.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_summary_state': 'pending',
                    'assemblyai_summary_next_retry_at': fields.Datetime.now(),
                    'assemblyai_summary_attempts': 0,
                    'assemblyai_summary_error': False,
                })
        return True

    def _assemblyai_generate_summary(self, transcript=None):
        """Create or merge one Arabic summary in one AI-agent call."""
        self.ensure_one()
        from odoo.tools import html2plaintext

        transcript_text = (transcript or self.transcript or '').strip()
        if not transcript_text:
            return False
        sembly_text = html2plaintext(self.summary or '').strip()
        # Four hours of ordinary conversation generally fit below this. The
        # cap protects the configured agent's context window from pathological
        # transcripts while retaining substantially more than Gemini notes do.
        transcript_text = transcript_text[:180000]
        if sembly_text:
            generated = html2plaintext(
                self.assemblyai_summary or '').strip() \
                if self.assemblyai_summary_source == 'generated' else ''
            assembly_text = generated or transcript_text
            assembly_label = ('ملخص AssemblyAI' if generated
                              else 'تفريغ AssemblyAI')
            prompt = (
                "لديك ملخص Sembly ومحتوى AssemblyAI للاجتماع نفسه. أنشئ نسخة "
                "واحدة متكاملة بلا تكرار. احتفظ بالمحاور والقرارات والمهام "
                "والمسؤولين والمواعيد والمخاطر والخطوات التالية، ولا تضف أي "
                "معلومة غير موجودة. عند التعارض نبّه عليه بإيجاز. أعد JSON فقط "
                "بالشكل {\"arabic_html\": \"<h4>...</h4>\"}.\n\n"
                "=== ملخص Sembly ===\n%s\n\n=== %s ===\n%s"
                % (sembly_text[:12000], assembly_label, assembly_text))
            source, operation = 'merged', 'assemblyai-summary-merge'
        else:
            prompt = (
                "أنشئ ملخصاً عربياً دقيقاً ومتكاملاً من تفريغ الاجتماع أدناه. "
                "نظّم الناتج في HTML بسيط بعناوين ونقاط، وغطّ المحاور والقرارات "
                "والمهام والمسؤولين والمواعيد والمخاطر والخطوات التالية. لا "
                "تستنتج أو تضف معلومات غير موجودة، واحذف التكرار والكلام غير "
                "الجوهري. أعد JSON فقط بالشكل "
                "{\"arabic_html\": \"<h4>...</h4>\"}.\n\n"
                "=== تفريغ AssemblyAI ===\n%s" % transcript_text)
            source, operation = 'generated', 'assemblyai-summary'
        try:
            data = self._extract_json(self._ask_agent(prompt)) or {}
            html = self._coerce_html(data.get('arabic_html'))
            if not html:
                raise UserError(_("AI agent returned no meeting summary"))
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_summary': html,
                'assemblyai_summary_source': source,
                'assemblyai_summary_state': 'completed',
                'assemblyai_summary_attempts': 0,
                'assemblyai_summary_next_retry_at': False,
                'assemblyai_summary_error': False,
            })
            return True
        except Exception as exc:  # noqa: BLE001 - transcript remains successful
            attempts = self.assemblyai_summary_attempts + 1
            delays = [5, 15, 60, 240, 1440]
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_summary_state': 'failed',
                'assemblyai_summary_attempts': attempts,
                'assemblyai_summary_next_retry_at': fields.Datetime.now() + timedelta(
                    minutes=delays[min(attempts - 1, len(delays) - 1)]),
                'assemblyai_summary_error': str(exc)[:500],
            })
            self.env['sembly.sync.log']._log(
                'ai', operation, 'error',
                "meeting %s: %s" % (self.sembly_meeting_id, str(exc)[:300]))
            return False

    def _assemblyai_submit_recording(self):
        self.ensure_one()
        now = fields.Datetime.now()
        if self.transcript:
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_state': 'skipped_sembly',
                'assemblyai_provider': 'sembly',
            })
            return False
        if not self.assemblyai_manual_request and \
                self._assemblyai_is_too_old(self.started_at, now):
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_state': 'too_old'})
            return False
        if not self.assemblyai_manual_request and self.duration_seconds and \
                self.duration_seconds < MIN_DURATION_SECONDS:
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_state': 'too_short',
                'assemblyai_error': _("Recording is shorter than 10 minutes"),
            })
            return False
        limit = max(0, self._icp_int('sembly.assemblyai_monthly_hours', 150)) * 3600
        # Unknown duration reserves the API's 10-hour per-file maximum. This
        # may postpone a file near the cap, but can never overspend the cap.
        estimate = self.duration_seconds or 10 * 3600
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ['sembly.assemblyai.monthly_budget'])
        if limit and self._assemblyai_month_usage_seconds(
                exclude_ids=self.ids) + estimate > limit:
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_state': 'budget_blocked',
                'assemblyai_error': _("Monthly transcription limit reached"),
            })
            return False

        self.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'uploading', 'assemblyai_error': False,
            'assemblyai_requested_at': now,
            'assemblyai_audio_seconds': estimate,
            'assemblyai_estimated_cost': estimate / 3600.0 * PRICE_PER_HOUR})
        if self._may_commit():
            self.env.cr.commit()
        try:
            google = self._google_client(subject=self.google_owner_email or None)
            region = self._icp('sembly.assemblyai_region', 'us') or 'us'
            assembly = self._assemblyai_client(region=region)
            fingerprint = hashlib.sha256(
                self._assemblyai_api_key().encode()).hexdigest()
            with tempfile.TemporaryDirectory(prefix='sembly-audio-') as directory:
                os.chmod(directory, 0o700)
                source = os.path.join(directory, 'recording.mp4')
                with open(source, 'xb') as destination:
                    os.chmod(source, 0o600)
                    google.download_file_to(
                        self.google_file_id, destination,
                        max_bytes=(MAX_SOURCE_BYTES if ffmpeg_executable()
                                   else MAX_UPLOAD_BYTES))
                media = prepare_audio(source, directory)
                if os.path.getsize(media) > MAX_UPLOAD_BYTES:
                    raise UserError(_(
                        "The private recording exceeds AssemblyAI's upload limit; "
                        "install ffmpeg so only its audio track is uploaded."))
                with open(media, 'rb') as stream:
                    upload_url = assembly.upload_file(stream)
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'submitting',
                    'assemblyai_upload_url': upload_url,
                    'assemblyai_region': region,
                    'assemblyai_key_fingerprint': fingerprint,
                    'assemblyai_submitting_at': fields.Datetime.now(),
                    'assemblyai_audio_channels': 1,
                })
                if self._may_commit():
                    self.env.cr.commit()
                # Download/upload can take minutes. Sembly may have delivered
                # while they ran, so re-read before the billable submit call.
                self.invalidate_recordset(['transcript'])
                if self.transcript:
                    self.sudo().with_context(sembly_sync=True).write({
                        'assemblyai_state': 'skipped_sembly',
                        'assemblyai_provider': 'sembly',
                        'assemblyai_upload_url': False,
                    })
                    return False
                if not self.assemblyai_manual_request and \
                        self._assemblyai_is_too_old(self.started_at):
                    self.sudo().with_context(sembly_sync=True).write({
                        'assemblyai_state': 'too_old',
                        'assemblyai_upload_url': False})
                    return False
                # A long transfer can cross into a new month. Move the budget
                # reservation to the month that will actually be billed.
                billed_at = fields.Datetime.now()
                self.env.cr.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ['sembly.assemblyai.monthly_budget'])
                limit = max(0, self._icp_int(
                    'sembly.assemblyai_monthly_hours', 150)) * 3600
                if limit and self._assemblyai_month_usage_seconds(
                        exclude_ids=self.ids) + estimate > limit:
                    self.sudo().with_context(sembly_sync=True).write({
                        'assemblyai_state': 'budget_blocked',
                        'assemblyai_upload_url': False,
                        'assemblyai_error': _("Monthly transcription limit reached"),
                    })
                    return False
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_requested_at': billed_at})
                transcript_id = assembly.submit(upload_url)
            # Serialize the tiny state handoff with a possible Sembly webhook.
            # If it arrived during submit, retain the id only so cron can delete
            # the now-unneeded remote job; never overwrite the Sembly text.
            self.env.cr.execute(
                "SELECT id FROM sembly_meeting WHERE id = %s FOR UPDATE",
                [self.id])
            self.invalidate_recordset(['transcript'])
            superseded = bool(self.transcript)
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_state': 'cancel_pending' if superseded else 'processing',
                'assemblyai_transcript_id': transcript_id,
                'assemblyai_upload_url': False,
                'assemblyai_provider': 'sembly' if superseded else False,
                'assemblyai_requested_at': billed_at,
                'assemblyai_audio_seconds': estimate,
                'assemblyai_audio_channels': 1,
                'assemblyai_estimated_cost': estimate / 3600.0 * PRICE_PER_HOUR,
                'assemblyai_attempts': 0,
                'assemblyai_next_retry_at': False,
            })
            return True
        except Exception as exc:  # noqa: BLE001 - cron must survive provider/media errors
            if self.assemblyai_state == 'submitting' and \
                    isinstance(exc, AssemblyAIError) and \
                    (exc.uncertain or exc.retryable):
                # Never blindly resubmit: the remote POST may have succeeded.
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_error': str(exc)[:500]})
            else:
                if self.assemblyai_state == 'submitting':
                    self.sudo().with_context(sembly_sync=True).write({
                        'assemblyai_upload_url': False})
                self._assemblyai_fail(exc)
            return False

    def _assemblyai_recover_submit(self, allow_submit=True):
        self.ensure_one()
        if not self.assemblyai_upload_url:
            self._assemblyai_fail(AssemblyAIError(
                "Cannot recover submit without its private upload URL"))
            return
        client = self._assemblyai_client(region=self.assemblyai_region or 'us')
        try:
            dates = [fields.Datetime.now().date()]
            if self.assemblyai_submitting_at:
                dates.append(self.assemblyai_submitting_at.date())
            found = client.find_transcript(
                self.assemblyai_upload_url, created_on=dates)
            if found and found.get('id'):
                created = self._parse_dt(found.get('created')) or fields.Datetime.now()
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_transcript_id': found['id'],
                    'assemblyai_upload_url': False,
                    'assemblyai_requested_at': created,
                    'assemblyai_state': ('cancel_pending'
                                         if self.assemblyai_provider == 'sembly'
                                         else 'processing'),
                })
                return
            if self.assemblyai_provider == 'sembly':
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'skipped_sembly',
                    'assemblyai_upload_url': False,
                })
                return
            if not allow_submit:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'failed',
                    'assemblyai_upload_url': False,
                    'assemblyai_error': _(
                        "Submit was not found and new submissions are disabled"),
                })
                return
            if not self.assemblyai_manual_request and \
                    self._assemblyai_is_too_old(self.started_at):
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'too_old',
                    'assemblyai_upload_url': False,
                })
                return
            if not self.assemblyai_manual_request and self.duration_seconds and \
                    self.duration_seconds < MIN_DURATION_SECONDS:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'too_short',
                    'assemblyai_upload_url': False,
                    'assemblyai_error': _(
                        "Recording is shorter than 10 minutes"),
                })
                return
            estimate = self.assemblyai_audio_seconds or self.duration_seconds \
                or 10 * 3600
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ['sembly.assemblyai.monthly_budget'])
            limit = max(0, self._icp_int(
                'sembly.assemblyai_monthly_hours', 150)) * 3600
            if limit and self._assemblyai_month_usage_seconds(
                    exclude_ids=self.ids) + estimate > limit:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'budget_blocked',
                    'assemblyai_upload_url': False,
                    'assemblyai_error': _("Monthly transcription limit reached"),
                })
                return
            billed_at = fields.Datetime.now()
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_requested_at': billed_at})
            transcript_id = client.submit(self.assemblyai_upload_url)
            self.sudo().with_context(sembly_sync=True).write({
                'assemblyai_transcript_id': transcript_id,
                'assemblyai_upload_url': False,
                'assemblyai_state': 'processing',
            })
        except AssemblyAIError as exc:
            if exc.uncertain or exc.retryable:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_error': str(exc)[:500]})
            else:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_upload_url': False})
                self._assemblyai_fail(exc)

    def _assemblyai_delete_remote(self, client=None):
        self.ensure_one()
        if not self.assemblyai_transcript_id or self.assemblyai_remote_deleted:
            return True
        try:
            (client or self._assemblyai_client(
                region=self.assemblyai_region or 'us')).delete_transcript(
                self.assemblyai_transcript_id)
        except AssemblyAIError as exc:
            current = hashlib.sha256(
                self._assemblyai_api_key().encode()).hexdigest()
            if exc.status != 404 or not self.assemblyai_key_fingerprint \
                    or self.assemblyai_key_fingerprint != current:
                raise
        self.sudo().with_context(sembly_sync=True).write({
            'assemblyai_remote_deleted': True})
        return True

    def _assemblyai_poll(self):
        self.ensure_one()
        client = self._assemblyai_client(region=self.assemblyai_region or 'us')
        if self.assemblyai_state == 'cancel_pending':
            try:
                self._assemblyai_delete_remote(client)
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_state': 'superseded',
                    'assemblyai_provider': 'sembly',
                })
            except AssemblyAIError as exc:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_error': str(exc)[:500]})
            return
        try:
            data = client.get_transcript(self.assemblyai_transcript_id)
            if data['status'] in ('queued', 'processing'):
                return
            if data['status'] == 'error':
                raise AssemblyAIError(data.get('error') or 'Transcription failed')
            text, speakers = self._assemblyai_format_transcript(data)
            if not text:
                raise AssemblyAIError("AssemblyAI completed without transcript text")
            seconds = int(data.get('audio_duration') or self.assemblyai_audio_seconds or 0)
            values = {
                'assemblyai_state': 'completed',
                'assemblyai_completed_at': fields.Datetime.now(),
                'assemblyai_model': data.get('speech_model_used') or 'universal-2',
                'assemblyai_language_code': data.get('language_code') or 'ar',
                'assemblyai_audio_seconds': seconds,
                'assemblyai_estimated_cost': seconds / 3600.0 * PRICE_PER_HOUR,
                'assemblyai_speaker_count': speakers,
                'assemblyai_error': False,
            }
            summarize = not bool(self.transcript)
            if summarize:
                values.update({'transcript': text,
                               'assemblyai_provider': 'assemblyai',
                               'assemblyai_summary_state': 'pending',
                               'assemblyai_summary_next_retry_at': fields.Datetime.now()})
            else:
                values.update({'assemblyai_state': 'superseded',
                               'assemblyai_provider': 'sembly'})
            self.sudo().with_context(sembly_sync=True).write(values)
            if self._may_commit():
                self.env.cr.commit()
            try:
                self._assemblyai_delete_remote(client)
            except AssemblyAIError as exc:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_error': str(exc)[:500]})
            if summarize:
                self._assemblyai_generate_summary(transcript=text)
        except AssemblyAIError as exc:
            if exc.retryable:
                self.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_error': str(exc)[:500]})
            else:
                self._assemblyai_fail(exc)

    @api.model
    def _cron_assemblyai_transcriptions(self):
        now = fields.Datetime.now()
        summaries = self.sudo().search([
            ('assemblyai_summary_state', 'in', ['pending', 'failed']),
            ('assemblyai_summary_attempts', '<', 5),
            ('transcript', '!=', False),
            '|', ('assemblyai_summary_next_retry_at', '=', False),
            ('assemblyai_summary_next_retry_at', '<=', now),
        ], order='assemblyai_summary_next_retry_at, id', limit=1)
        if summaries:
            summaries._assemblyai_generate_summary()

        # Disabling stops new submissions, never summary retries or retention
        # cleanup for jobs already sent. A key is still required to poll/delete.
        if not self._assemblyai_api_key():
            return True

        enabled = self._assemblyai_enabled()
        recovering = self.sudo().search([
            ('assemblyai_state', '=', 'submitting'),
            ('assemblyai_submitting_at', '<=', now - timedelta(minutes=10)),
        ], limit=10)
        for record in recovering:
            record._assemblyai_recover_submit(allow_submit=enabled)

        if not enabled:
            for record in self.sudo().search([
                    ('assemblyai_state', 'in', list(ACTIVE_REMOTE_STATES)),
                    ('assemblyai_transcript_id', '!=', False)], limit=10):
                record._assemblyai_poll()
            for record in self.sudo().search([
                    ('assemblyai_state', 'in', list(TERMINAL_STATES)),
                    ('assemblyai_transcript_id', '!=', False),
                    ('assemblyai_remote_deleted', '=', False)], limit=10):
                try:
                    record._assemblyai_delete_remote()
                except AssemblyAIError as exc:
                    record.sudo().with_context(sembly_sync=True).write({
                        'assemblyai_error': str(exc)[:500]})
            return True

        waiting = self.sudo().search([
            ('assemblyai_state', '=', 'waiting_sembly'),
            ('assemblyai_due_at', '<=', now),
        ], limit=20)
        for record in waiting:
            record.sudo().with_context(sembly_sync=True).write({
                'assemblyai_state': ('skipped_sembly' if record.transcript else 'queued'),
                'assemblyai_provider': 'sembly' if record.transcript else False,
            })

        retryable = self.sudo().search([
            ('assemblyai_state', 'in', ['failed', 'budget_blocked']),
            ('assemblyai_transcript_id', '=', False),
            ('assemblyai_attempts', '<', 5),
            '|', ('assemblyai_next_retry_at', '=', False),
            ('assemblyai_next_retry_at', '<=', now),
        ], limit=20)
        retryable._assemblyai_maybe_queue()

        stale = self.sudo().search([
            ('assemblyai_state', '=', 'uploading'),
            ('assemblyai_requested_at', '<=', now - timedelta(hours=2)),
            ('assemblyai_transcript_id', '=', False),
        ], limit=10)
        stale.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'failed',
            'assemblyai_next_retry_at': now,
            'assemblyai_error': _("Upload worker stopped before submission"),
        })

        for record in self.sudo().search([
                ('assemblyai_state', 'in', list(ACTIVE_REMOTE_STATES)),
                ('assemblyai_transcript_id', '!=', False)], limit=10):
            record._assemblyai_poll()

        cleanup = self.sudo().search([
            ('assemblyai_state', 'in', list(TERMINAL_STATES)),
            ('assemblyai_transcript_id', '!=', False),
            ('assemblyai_remote_deleted', '=', False),
        ], limit=10)
        for record in cleanup:
            try:
                record._assemblyai_delete_remote()
            except AssemblyAIError as exc:
                record.sudo().with_context(sembly_sync=True).write({
                    'assemblyai_error': str(exc)[:500]})

        queued = self.sudo().search([
            ('assemblyai_state', '=', 'queued'),
            ('assemblyai_due_at', '<=', now),
        ], order='assemblyai_due_at, id', limit=1)
        if queued:
            queued._assemblyai_submit_recording()
        return True

    def action_assemblyai_retry(self):
        if not self.env.user.has_group('era_sembly_meetings.group_sembly_manager'):
            raise AccessError(_("Only a Sembly manager can retry transcription."))
        for record in self:
            if record.assemblyai_state == 'uncertain':
                raise UserError(_(
                    "The previous submit may have succeeded. Resolve its remote "
                    "state before retrying to avoid duplicate billing."))
            if record.assemblyai_transcript_id and not record.assemblyai_remote_deleted:
                record._assemblyai_delete_remote()
            record.sudo().with_context(sembly_sync=True).write({
                'assemblyai_state': False,
                'assemblyai_transcript_id': False,
                'assemblyai_upload_url': False,
                'assemblyai_remote_deleted': False,
                'assemblyai_error': False,
                'assemblyai_next_retry_at': False,
            })
            record._assemblyai_maybe_queue()
        return True

    def action_assemblyai_retry_summary(self):
        if not self.env.user.has_group('era_sembly_meetings.group_sembly_manager'):
            raise AccessError(_("Only a Sembly manager can retry summarization."))
        for record in self:
            record.sudo().with_context(sembly_sync=True).write({
                'assemblyai_summary_state': 'pending',
                'assemblyai_summary_attempts': 0,
                'assemblyai_summary_next_retry_at': False,
                'assemblyai_summary_error': False,
            })
            record._assemblyai_generate_summary()
        return True

    def action_assemblyai_request_now(self):
        """Bypass the Sembly wait while preserving every billing guard."""
        self.ensure_one()
        allowed = (
            self.env.user.has_group(
                'era_sembly_meetings.group_sembly_manager')
            or self.env.user.has_group('project.group_project_manager'))
        if not allowed:
            raise AccessError(_(
                "Only a meeting manager or project manager can request "
                "transcription."))
        if not self._assemblyai_enabled():
            raise UserError(_(
                "AssemblyAI is disabled or its API key is not configured."))
        if self.transcript:
            raise UserError(_("This meeting already has a transcript."))
        if not self.google_file_id:
            raise UserError(_("This meeting has no Google recording."))
        if self.assemblyai_state in (
                'uploading', 'submitting', 'processing', 'cancel_pending'):
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {
                    'title': _("AssemblyAI"), 'type': 'info',
                    'message': _("Transcription is already in progress."),
                },
            }
        if self.assemblyai_state == 'uncertain' or (
                self.assemblyai_transcript_id
                and not self.assemblyai_remote_deleted):
            raise UserError(_(
                "A previous remote request may still exist. Resolve it before "
                "creating another billable request."))
        now = fields.Datetime.now()
        self.sudo().with_context(sembly_sync=True).write({
            'assemblyai_state': 'queued',
            'assemblyai_due_at': now,
            'assemblyai_imported_at': self.assemblyai_imported_at or now,
            'assemblyai_attempts': 0,
            'assemblyai_next_retry_at': False,
            'assemblyai_error': False,
            'assemblyai_transcript_id': False,
            'assemblyai_remote_deleted': False,
            'assemblyai_manual_request': True,
        })
        cron = self.env.ref(
            'era_sembly_meetings_assemblyai.cron_sembly_assemblyai_transcriptions',
            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _("AssemblyAI"), 'type': 'success',
                'message': _(
                    "The meeting was queued for immediate transcription."),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    @api.model
    def _sembly_content_fields(self):
        return super()._sembly_content_fields() | {
            name for name in self._fields if name.startswith('assemblyai_')
        }
