import re
from json.decoder import JSONDecodeError
from logging import getLogger

from psycopg2 import errors
from requests.exceptions import RequestException

from odoo import _, api, fields, models, modules
from odoo.exceptions import UserError

from odoo.addons.era_voip_ext.utils.llm_api_service import LLMApiService

_logger = getLogger(__name__)


class VoipCall(models.Model):
    _inherit = "voip.call"

    recording_url = fields.Char(
        string="Recording",
        compute="_compute_recording_url",
    )
    recording_link_html = fields.Html(
        string="Recording",
        compute="_compute_recording_url",
        sanitize=False,
    )

    def _commit_if_needed(self):
        if not modules.module.current_test:
            self.env.cr.commit()

    def _safe_write(self, call, vals):
        if not call:
            return False
        call_id = call.id
        retries = 3
        for attempt in range(1, retries + 1):
            try:
                with self.env.cr.savepoint():
                    target = self.browse(call_id).exists()
                    if not target:
                        return False
                    target.write(vals)
                return True
            except errors.SerializationFailure as exc:
                self.env.invalidate_all()
                if attempt < retries:
                    _logger.info(
                        "Call %s: concurrent update while writing %s, retrying (%s/%s)",
                        call_id,
                        ",".join(vals.keys()),
                        attempt,
                        retries,
                    )
                    continue
                _logger.warning(
                    "Call %s: write failed after retries due to concurrent update: %s",
                    call_id,
                    exc,
                )
                return False
            except Exception as exc:
                _logger.warning("Call %s: write failed: %s", call_id, exc)
                return False


    @api.model
    def _get_next_pending_transcription_call(self):
        self.env.cr.execute(
            f"""
                SELECT id
                  FROM {self._table}
                 WHERE transcription_status = %s
              ORDER BY create_date DESC
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
            """,
            ("pending",),
        )
        row = self.env.cr.fetchone()
        return self.browse(row[0]) if row else self.browse()

    @api.model
    def _cron_transcribe_recent_voip_call(self):
        call = self._get_next_pending_transcription_call()
        if not call:
            return
        self._transcribe_call(call)

    def _transcribe_call(self, call):
        if not self._safe_write(call, {"transcription_status": "queued"}):
            return
        domain = [
            ("res_model", "=", "voip.call"),
            ("res_id", "=", call.id),
            ("mimetype", "=", "audio/ogg"),
        ]
        recordings = self.env["ir.attachment"].search(domain, order="create_date desc")

        if not recordings:
            self._safe_write(call, {"transcription_status": "no_audio"})
            return

        if len(recordings) > 1:
            _logger.warning(
                "Call %s has multiple recordings; processing only the newest one (%s).",
                call.id,
                recordings[0].id,
            )

        recording = recordings[0]
        recording_data = recording.raw
        self._commit_if_needed()
        prompt = "Output should be in Arabic and formatted as a call conversation between an employee and a customer based on the following text. example: [employee name] : [the script]. then new line then [customer name] : [the script]. "

        try:
            text = LLMApiService(self.env).get_transcription(
                recording_data,
                "audio/ogg",
                #prompt=prompt,
            )
        except (RequestException, JSONDecodeError, UserError):
            _logger.exception("Call %s: transcription failed", call.id)
            self._commit_if_needed()
            self._safe_write(call, {"transcription_status": "error"})
            return

        self._commit_if_needed()
        text = (text or "").strip()
        if not text or re.fullmatch(r"[.\s]+", text):
            _logger.warning("Call %s: empty/placeholder transcript", call.id)
            self._safe_write(call, {"transcription_status": "error"})
            return
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines and all(re.fullmatch(r"Speaker\s*\d+:[.\s]*", line) for line in lines):
            _logger.warning("Call %s: placeholder-only transcript", call.id)
            self._safe_write(call, {"transcription_status": "error"})
            return

        if re.search(r"Speaker\s*\d+:", text):
            text = re.sub(r"\s*(Speaker\s*\d+:)", r"\n\1", text).strip()

        text1 = self._format_transcript_with_agent(text) or ""
        self._commit_if_needed()
        transcript = text1 + "\n ------------ \n" + text

        # Generate one-liner summary
        summary = None
        try:
            ai_agent = self.env.ref("voip_ai.voip_call_summary_agent", raise_if_not_found=False)
            if ai_agent and transcript:
                summary_response = ai_agent.get_direct_response(prompt=text)
                if summary_response:
                    summary = summary_response[0]
        except (RequestException, JSONDecodeError, UserError):
            _logger.exception("Call %s: one-liner summary generation failed", call.id)
        self._commit_if_needed()

        vals = {
            "transcript": transcript,
            "transcription_status": "done",
        }
        if summary:
            vals["summary"] = summary
        self._safe_write(call, vals)

    def _format_transcript_with_agent(self, text):
        if not text:
            return text
        prompt = (
            "Output should be in Arabic and formatted as a call conversation between an employee and a customer based on the following text. "
            "example: [employee name] : [the script]. then new line [customer name] : [the script]. if you can't recognize the names, use 'الموظف' for employee and 'العميل' for customer. "
        )
        try:
            ai_agent = self.env.ref("era_voip_ext.voip_call_formatting_agent", raise_if_not_found=False)
            if not ai_agent:
                return text
            response = ai_agent.get_direct_response(prompt=f"{prompt}\n\n{text}")
            if response:
                formatted = response[0]
                lines = [line.strip() for line in formatted.splitlines() if line.strip()]
                return "\n".join(lines)
        except (RequestException, JSONDecodeError, UserError):
            _logger.exception("Call %s: transcript formatting failed", self.id)
        return "..."

    def action_retranscript(self):
        self.ensure_one()
        if self.transcription_status == "queued":
            raise UserError(_("This call is already being processed."))
        self._transcribe_call(self)
        return True

    def action_retranscript_bulk(self):
        processed = 0
        skipped = 0
        for call in self:
            if call.transcription_status == "queued":
                skipped += 1
                continue
            self._transcribe_call(call)
            processed += 1

        message = _("Processed %(processed)s call(s).", processed=processed)
        if skipped:
            message += _(" Skipped %(skipped)s queued call(s).", skipped=skipped)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Transcript Bulk Action"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

    

    def _find_recording_attachment(self):
        self.ensure_one()
        Attachment = self.env["ir.attachment"].sudo()
        audio_domain = [
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            "|",
            ("mimetype", "ilike", "audio/"),
            ("name", "=ilike", "%.webm"),
        ]
        attachment = Attachment.search(
            audio_domain, order="create_date desc, id desc", limit=1
        )
        if not attachment:
            attachment = Attachment.search(
                [("res_model", "=", self._name), ("res_id", "=", self.id)],
                order="create_date desc, id desc",
                limit=1,
            )
        if attachment:
            return attachment
        attachment = Attachment.search(
            [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("name", "=ilike", "recording.webm"),
            ],
            order="create_date desc, id desc",
            limit=1,
        )
        if attachment:
            return attachment

        if "message_ids" in self._fields:
            messages = self.sudo().message_ids
            attachments = messages.mapped("attachment_ids")
            if attachments:
                audio_attachments = attachments.filtered(
                    lambda a: (a.mimetype or "").startswith("audio/")
                    or (a.name or "").lower().endswith(".webm")
                )
                candidates = audio_attachments or attachments
                candidates = candidates.sorted(
                    key=lambda a: (a.create_date or a.write_date, a.id),
                    reverse=True,
                )
                return candidates[:1]
        Message = self.env["mail.message"].sudo()
        message_model_field = "model" if "model" in Message._fields else "res_model"
        message_domain = [
            (message_model_field, "=", self._name),
            ("res_id", "=", self.id),
        ]
        messages = Message.search(message_domain)
        if messages:
            attachments = messages.mapped("attachment_ids")
            if attachments:
                audio_attachments = attachments.filtered(
                    lambda a: (a.mimetype or "").startswith("audio/")
                    or (a.name or "").lower().endswith(".webm")
                )
                candidates = audio_attachments or attachments
                candidates = candidates.sorted(
                    key=lambda a: (a.create_date or a.write_date, a.id),
                    reverse=True,
                )
                return candidates[:1]
        attachment = Attachment.search(
            [("name", "ilike", f"voip-call-{self.id}.")],
            order="create_date desc, id desc",
            limit=1,
        )
        if attachment:
            return attachment
        attachment = Attachment.search(
            [("name", "=ilike", "recording.webm")],
            order="create_date desc, id desc",
            limit=1,
        )
        if attachment:
            return attachment
        return False

    def _find_realtime_summary(self):
        self.ensure_one()
        Summary = self.env.get("crm.realtime_call_summary")
        if not Summary:
            return False
        summary_fields = Summary._fields
        for name in ("realtime_call_summary_id", "call_summary_id", "summary_id"):
            field = self._fields.get(name)
            if field and getattr(field, "comodel_name", None) == "crm.realtime_call_summary":
                summary = self[name]
                if summary:
                    return summary
        if "sip_call_id" in self._fields and "sip_call_id" in summary_fields and self.sip_call_id:
            summary = Summary.search(
                [("sip_call_id", "=", self.sip_call_id)],
                order="create_date desc, id desc",
                limit=1,
            )
            if summary:
                return summary
        if self.id and "sip_call_id" in summary_fields:
            summary = Summary.search(
                [
                    ("sip_call_id", "in", [f"voip:{self.id}", str(self.id)]),
                ],
                order="create_date desc, id desc",
                limit=1,
            )
            if summary:
                return summary
        if "call_id" in self._fields and "sip_call_id" in summary_fields and self.call_id:
            summary = Summary.search(
                [("sip_call_id", "=", self.call_id)],
                order="create_date desc, id desc",
                limit=1,
            )
            if summary:
                return summary
        if "lead_id" in self._fields and "lead_id" in summary_fields and self.lead_id:
            summary = Summary.search(
                [("lead_id", "=", self.lead_id.id)],
                order="create_date desc, id desc",
                limit=1,
            )
            if summary:
                return summary
        phone_fields = ("phone", "partner_phone", "contact_phone", "caller_phone")
        if "caller_phone" in summary_fields:
            for name in phone_fields:
                if name in self._fields and self[name]:
                    summary = Summary.search(
                        [("caller_phone", "=", self[name])],
                        order="create_date desc, id desc",
                        limit=1,
                    )
                    if summary:
                        return summary
        if "partner_id" in self._fields and self.partner_id:
            phones = [self.partner_id.phone, self.partner_id.mobile]
            phones = [phone for phone in phones if phone]
            if phones and "caller_phone" in summary_fields:
                summary = Summary.search(
                    [("caller_phone", "in", phones)],
                    order="create_date desc, id desc",
                    limit=1,
                )
                if summary:
                    return summary
        return False

    def _compute_recording_url(self):
        base_url = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        ).rstrip("/")

        def _absolute_url(url):
            if not url or not base_url:
                return url
            if url.startswith(("http://", "https://", "//")):
                return url
            if url.startswith("/"):
                return f"{base_url}{url}"
            return f"{base_url}/{url}"

        attachment_field = None
        for name in (
            "recording_attachment_id",
            "recording_id",
            "recording_attachment",
            "audio_attachment_id",
        ):
            field = self._fields.get(name)
            if field and getattr(field, "comodel_name", None) == "ir.attachment":
                attachment_field = name
                break
        if not attachment_field:
            for name, field in self._fields.items():
                if (
                    getattr(field, "comodel_name", None) == "ir.attachment"
                    and "record" in name
                ):
                    attachment_field = name
                    break

        binary_field = None
        for name in (
            "recording",
            "recording_data",
            "recording_file",
            "recording_binary",
            "audio",
            "audio_data",
        ):
            field = self._fields.get(name)
            if field and field.type == "binary":
                binary_field = name
                break
        if not binary_field:
            for name, field in self._fields.items():
                if getattr(field, "type", None) == "binary" and "record" in name:
                    binary_field = name
                    break

        for call in self:
            url = ""
            summary = call._find_realtime_summary()
            if summary:
                url = f"/realtime_agent/recording/{summary.id}"
            if attachment_field:
                attachment = call[attachment_field]
                if attachment:
                    url = (
                        f"/web/content/ir.attachment/{attachment.id}/datas?download=0&inline=1"
                    )
            if not url and binary_field and call.id and call[binary_field]:
                url = (
                    f"/web/content/{call._name}/{call.id}/{binary_field}?download=0&inline=1"
                )
            if not url and call.id:
                attachment = call._find_recording_attachment()
                if attachment:
                    url = f"/voip_call/recording/{call.id}"
            call.recording_url = _absolute_url(url)
            if call.recording_url:
                call.recording_link_html = (
                    f'<a href="{call.recording_url}" target="_blank">▶️</a>'
                )
            else:
                call.recording_link_html = ""
