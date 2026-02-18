# -*- coding: utf-8 -*-
import base64
import io
import json
from html import escape
import os
import re
import tempfile
import uuid
import requests

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


class RealtimeAgentController(http.Controller):
    MAX_AUDIO_BYTES = 40 * 1024 * 1024
    MAX_TRANSCRIBE_AUDIO_BYTES = 24 * 1024 * 1024

    def _get_realtime_model(self, ICP):
        """Return the configured model or the default gpt-realtime."""
        configured_model = ICP.get_param("openai.realtime_model")
        return configured_model or "gpt-realtime"

    def _summarize_transcript(self, api_key, transcript, system_prompt=None):
        url = "https://api.openai.com/v1/responses"
        prompt_text = system_prompt or "لخص المكالمة الواردة بالعربية بشكل قصير ومباشر جدًا. 2-3 نقاط كحد أقصى، واذكر أي إجراء مطلوب إن وجد."
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "input": [
                {
                    "role": "system",
                    "content": prompt_text,
                },
                {"role": "user", "content": transcript},
            ],
            "max_output_tokens": 300,
        }
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
        if r.status_code >= 400:
            return None, f"OpenAI summary failed: {r.status_code} {r.text}"
        try:
            data = r.json()
        except Exception as e:
            return None, f"OpenAI summary invalid JSON: {e}"
        summary = data.get("output_text")
        if not summary:
            for item in data.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "output_text" and content.get("text"):
                        summary = content.get("text")
                        break
                if summary:
                    break
        if not summary:
            return None, f"OpenAI summary missing output: {data}"
        return summary, None

    def _find_lead(self, phone=None, company=None):
        Lead = request.env["crm.lead"].sudo()
        lead = None
        if phone:
            lead = Lead.search(
                ["|", ("phone", "ilike", phone), ("mobile", "ilike", phone)],
                order="write_date desc, create_date desc",
                limit=1,
            )
        if not lead and company:
            lead = Lead.search(
                ["|", ("partner_name", "ilike", company), ("name", "ilike", company)],
                order="write_date desc, create_date desc",
                limit=1,
            )
        return lead

    def _transcribe_audio(self, api_key, filename, mimetype, audio_bytes):
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        files = {
            "file": (filename, io.BytesIO(audio_bytes), mimetype),
        }
        data = {
            "model": "gpt-4o-mini-transcribe",
        }
        r = requests.post(url, headers=headers, files=files, data=data, timeout=30)
        if r.status_code >= 400:
            return None, f"OpenAI transcription failed: {r.status_code} {r.text}"
        try:
            payload = r.json()
        except Exception as e:
            return None, f"OpenAI transcription invalid JSON: {e}"
        text = payload.get("text")
        if not text:
            return None, f"OpenAI transcription missing text: {payload}"
        return text, None

    def _coerce_duration_seconds(self, value):
        try:
            if value in (None, "", False):
                return None
            return int(float(value))
        except Exception:
            return None

    def _normalize_session_key(self, value):
        key = (value or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", key):
            return key
        return ""

    def _chunk_dir(self):
        path = os.path.join(tempfile.gettempdir(), "era_website_voice_agent_ai_chunks")
        os.makedirs(path, exist_ok=True)
        return path

    def _chunk_file_path(self, session_key):
        key = self._normalize_session_key(session_key)
        if not key:
            return ""
        return os.path.join(self._chunk_dir(), f"{key}.webm")

    def _append_chunk_file(self, session_key, chunk_bytes):
        path = self._chunk_file_path(session_key)
        if not path:
            return False
        with open(path, "ab") as f:
            f.write(chunk_bytes)
        return True

    def _read_chunk_file(self, session_key):
        path = self._chunk_file_path(session_key)
        if not path or not os.path.exists(path):
            return b""
        with open(path, "rb") as f:
            return f.read()

    def _delete_chunk_file(self, session_key):
        path = self._chunk_file_path(session_key)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    def _summary_model(self):
        return request.env["crm.realtime_call_summary"].sudo()

    def _get_session_record(self, kwargs):
        Summary = self._summary_model()
        summary_id = kwargs.get("summary_id")
        session_key = (kwargs.get("session_key") or "").strip()
        if not summary_id or not session_key:
            return Summary.browse()
        try:
            summary_id = int(summary_id)
        except Exception:
            return Summary.browse()
        record = Summary.browse(summary_id).exists()
        if not record:
            return Summary.browse()
        if "session_key" in Summary._fields and record.session_key == session_key:
            return record
        return Summary.browse()

    def _upsert_summary_record(self, kwargs, values):
        Summary = self._summary_model()
        values = {k: v for k, v in values.items() if k in Summary._fields}
        record = self._get_session_record(kwargs)
        if record:
            record.write(values)
            return record
        session_key = (kwargs.get("session_key") or "").strip()
        if session_key and "session_key" in Summary._fields:
            values["session_key"] = session_key
        return Summary.create(values)

    def _save_summary_audio(self, kwargs, audio_bytes, filename, mimetype):
        if len(audio_bytes) > self.MAX_AUDIO_BYTES:
            return {"error": "Audio too large"}

        ICP = request.env["ir.config_parameter"].sudo()
        api_key = ICP.get_param("openai.api_key") or ""
        summary_prompt = ICP.get_param("openai.realtime_summary_prompt") or None

        fallback_transcript = (kwargs.get("transcript") or "").strip()
        transcript = ""
        error = None
        warnings = []
        if not api_key:
            warnings.append("Missing openai.api_key; skipping transcription and summary.")
        elif len(audio_bytes) > self.MAX_TRANSCRIBE_AUDIO_BYTES:
            warnings.append("Audio too large for transcription; recording saved without auto-transcript.")
        else:
            transcript, error = self._transcribe_audio(api_key, filename, mimetype, audio_bytes)
        if error:
            transcript = ""
            warnings.append(f"Transcription failed: {error}")
        if not transcript and fallback_transcript:
            transcript = fallback_transcript
            warnings.append("Using client transcript fallback.")

        summary, error = (None, None)
        if transcript and api_key:
            summary, error = self._summarize_transcript(api_key, transcript, summary_prompt)
        if error:
            summary = "تعذر تلخيص المكالمة تلقائيا."
            warnings.append(f"Summary failed: {error}")
        if not summary:
            summary = "تم حفظ تسجيل المكالمة."

        values = {
            "summary": summary,
            "transcription": transcript,
            "prompt_id": kwargs.get("prompt_id") or ICP.get_param("openai.realtime_prompt_id"),
            "prompt_version": ICP.get_param("openai.realtime_prompt_version"),
            "model": kwargs.get("model") or ICP.get_param("openai.realtime_model"),
            "voice": kwargs.get("voice") or ICP.get_param("openai.realtime_voice"),
            "duration_seconds": self._coerce_duration_seconds(kwargs.get("duration_seconds")),
            "call_source": "agent",
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        record = self._upsert_summary_record(kwargs, values)
        attachment = request.env["ir.attachment"].sudo().create(
            {
                "name": filename,
                "datas": base64.b64encode(audio_bytes),
                "mimetype": mimetype,
                "res_model": "crm.realtime_call_summary",
                "res_id": record.id,
            }
        )
        record.sudo().write({"attachment_id": attachment.id})
        self._delete_chunk_file(kwargs.get("session_key"))
        response = {"id": record.id, "summary": summary, "attachment_id": attachment.id}
        if warnings:
            response["warning"] = " | ".join(warnings)
        return response

    def _save_abandoned_summary(self, kwargs):
        ICP = request.env["ir.config_parameter"].sudo()
        existing = self._get_session_record(kwargs)
        if existing and existing.attachment_id:
            return {"id": existing.id, "summary": existing.summary}
        chunk_audio = self._read_chunk_file(kwargs.get("session_key"))
        if chunk_audio:
            return self._save_summary_audio(
                kwargs,
                chunk_audio,
                kwargs.get("audio_filename") or "realtime-call.webm",
                kwargs.get("audio_mimetype") or "audio/webm",
            )
        values = {
            "summary": "انتهت المكالمة بمغادرة الصفحة قبل اكتمال حفظ التسجيل.",
            "transcription": (kwargs.get("transcript") or "").strip(),
            "prompt_id": kwargs.get("prompt_id") or ICP.get_param("openai.realtime_prompt_id"),
            "prompt_version": ICP.get_param("openai.realtime_prompt_version"),
            "model": kwargs.get("model") or ICP.get_param("openai.realtime_model"),
            "voice": kwargs.get("voice") or ICP.get_param("openai.realtime_voice"),
            "duration_seconds": self._coerce_duration_seconds(kwargs.get("duration_seconds")),
            "call_source": "agent",
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        record = self._upsert_summary_record(kwargs, values)
        return {"id": record.id, "summary": values.get("summary")}

    def _prompt_has_mcp_tools(self, api_key, prompt_id):
        """Check if a prompt declares MCP tools to provide a clear error before session start."""
        if not prompt_id:
            return False, None
        url = f"https://api.openai.com/v1/prompts/{prompt_id}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "prompts=v1",
        }
        try:
            r = requests.get(url, headers=headers, timeout=10)
        except Exception as e:
            return False, f"OpenAI prompt lookup failed: {e}"
        if r.status_code in (401, 403):
            return False, f"OpenAI prompt lookup unauthorized: {r.status_code} {r.text}"
        if r.status_code >= 400:
            # Treat other failures as non-blocking to avoid breaking valid sessions.
            return False, None
        try:
            data = r.json()
        except Exception as e:
            return False, f"OpenAI prompt lookup invalid JSON: {e}"
        tools = data.get("tools") or (data.get("prompt") or {}).get("tools") or []
        for tool in tools:
            tool_type = (tool.get("type") or tool.get("tool_type") or "").lower()
            provider = (tool.get("provider") or "").lower()
            if tool_type == "mcp" or provider == "mcp":
                return True, None
        return False, None

    @http.route("/realtime_agent/token", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_token(self):
        """Return a short-lived token for browser clients to connect to the Realtime API."""
        ICP = request.env["ir.config_parameter"].sudo()
        api_key = ICP.get_param("openai.api_key")
        
        model = self._get_realtime_model(ICP)
        raw_voice = ICP.get_param("openai.realtime_voice")
        voice = raw_voice or "alloy"
        if voice == "marin":
            voice = "alloy"
        prompt_id = ICP.get_param("openai.realtime_prompt_id")
        prompt_version = ICP.get_param("openai.realtime_prompt_version")

        if not api_key:
            return {"error": "Missing system parameter: openai.api_key"}

        has_mcp_tools, prompt_check_error = self._prompt_has_mcp_tools(api_key, prompt_id)
        if has_mcp_tools:
            return {
                "error": "Prompt uses MCP tools",
                "details": "MCP tools must be fetched from the MCP server before use. Remove MCP tools from the prompt or ensure the MCP server is available and warmed.",
            }
        if prompt_check_error:
            return {"error": "Prompt lookup failed", "details": prompt_check_error}

        url = "https://api.openai.com/v1/realtime/sessions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "realtime=v1",
        }
        payload = {
            "model": model,
            "voice": voice,
            "modalities": ["audio", "text"],
            "instructions": "You are a helpful assistant.",
        }
        if prompt_id:
            payload["prompt"] = {"id": prompt_id}
            if prompt_version:
                payload["prompt"]["version"] = prompt_version

        try:
            r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
        except Exception as e:
            return {"error": f"OpenAI request failed: {e}"}

        if r.status_code >= 400:
            return {"error": "OpenAI token mint failed", "status": r.status_code, "details": r.text}

        data = r.json()
        client_secret = (data.get("client_secret") or {}).get("value")
        if not client_secret:
            return {"error": "No client_secret returned by OpenAI", "details": data}

        return {"value": client_secret}

    @http.route("/realtime_agent/session_start", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_session_start(self, **kwargs):
        ICP = request.env["ir.config_parameter"].sudo()
        Summary = self._summary_model()
        session_key = uuid.uuid4().hex
        values = {
            "summary": "بدأت مكالمة من الموقع.",
            "prompt_id": kwargs.get("prompt_id") or ICP.get_param("openai.realtime_prompt_id"),
            "prompt_version": ICP.get_param("openai.realtime_prompt_version"),
            "model": kwargs.get("model") or ICP.get_param("openai.realtime_model"),
            "voice": kwargs.get("voice") or ICP.get_param("openai.realtime_voice"),
            "duration_seconds": 0,
            "call_source": "agent",
            "caller_phone": kwargs.get("caller_phone") or "",
            "caller_company": kwargs.get("caller_company") or "",
        }
        lead = self._find_lead(phone=values["caller_phone"], company=values["caller_company"])
        if lead:
            values["lead_id"] = lead.id
        if "session_key" in Summary._fields:
            values["session_key"] = session_key
        values = {k: v for k, v in values.items() if k in Summary._fields}
        record = Summary.create(values)
        return {"summary_id": record.id, "session_key": session_key}

    @http.route("/realtime_agent/chunk", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_chunk(self, **kwargs):
        record = self._get_session_record(kwargs)
        if not record:
            return {"error": "Invalid session"}
        audio_chunk_base64 = (kwargs.get("audio_chunk_base64") or "").strip()
        if not audio_chunk_base64:
            return {"error": "Missing audio chunk"}
        try:
            chunk_bytes = base64.b64decode(audio_chunk_base64)
        except Exception as e:
            return {"error": "Invalid audio chunk", "details": str(e)}
        if not chunk_bytes:
            return {"error": "Empty audio chunk"}
        if len(chunk_bytes) > 2 * 1024 * 1024:
            return {"error": "Audio chunk too large"}
        if not self._append_chunk_file(kwargs.get("session_key"), chunk_bytes):
            return {"error": "Invalid session key"}
        return {"ok": True, "summary_id": record.id}

    @http.route("/realtime_agent/summary", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_summary(self, **kwargs):
        """Summarize the transcript and store it in CRM."""
        transcript = (kwargs.get("transcript") or "").strip()
        if not transcript:
            return {"error": "Missing transcript"}
        if len(transcript) > 20000:
            return {"error": "Transcript too long"}

        ICP = request.env["ir.config_parameter"].sudo()
        api_key = ICP.get_param("openai.api_key")
        if not api_key:
            return {"error": "Missing system parameter: openai.api_key"}

        summary_prompt = ICP.get_param("openai.realtime_summary_prompt") or None
        summary, error = self._summarize_transcript(api_key, transcript, summary_prompt)
        warning = None
        if error:
            summary = "تعذر تلخيص المكالمة تلقائيا."
            warning = f"Summary failed: {error}"

        values = {
            "summary": summary,
            "transcript": transcript,
            "prompt_id": kwargs.get("prompt_id") or ICP.get_param("openai.realtime_prompt_id"),
            "prompt_version": ICP.get_param("openai.realtime_prompt_version"),
            "model": kwargs.get("model") or ICP.get_param("openai.realtime_model"),
            "voice": kwargs.get("voice") or ICP.get_param("openai.realtime_voice"),
            "duration_seconds": self._coerce_duration_seconds(kwargs.get("duration_seconds")),
            "call_source": "agent",
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        record = self._upsert_summary_record(kwargs, values)
        response = {"id": record.id, "summary": summary}
        if warning:
            response["warning"] = warning
        return response

    @http.route("/realtime_agent/summary_audio", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_summary_audio(self, **kwargs):
        """Store recording, transcribe it, and save summary in CRM."""
        audio_base64 = (kwargs.get("audio_base64") or "").strip()
        if not audio_base64:
            return {"error": "Missing audio"}

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as e:
            return {"error": "Invalid audio data", "details": str(e)}

        filename = kwargs.get("audio_filename") or "realtime-call.webm"
        mimetype = kwargs.get("audio_mimetype") or "audio/webm"
        return self._save_summary_audio(kwargs, audio_bytes, filename, mimetype)

    @http.route(
        "/realtime_agent/summary_audio_beacon",
        type="http",
        auth="public",
        methods=["POST"],
        website=True,
        csrf=False,
    )
    def realtime_agent_summary_audio_beacon(self, **post):
        """Receive unload-safe multipart uploads from navigator.sendBeacon."""
        audio_file = request.httprequest.files.get("audio_file")
        if not audio_file:
            return request.make_response(
                json.dumps({"error": "Missing audio"}),
                headers=[("Content-Type", "application/json")],
                status=400,
            )
        try:
            audio_bytes = audio_file.read() or b""
        except Exception as e:
            return request.make_response(
                json.dumps({"error": "Invalid audio data", "details": str(e)}),
                headers=[("Content-Type", "application/json")],
                status=400,
            )

        filename = post.get("audio_filename") or audio_file.filename or "realtime-call.webm"
        mimetype = post.get("audio_mimetype") or getattr(audio_file, "mimetype", None) or "audio/webm"
        payload = dict(post)
        response = self._save_summary_audio(payload, audio_bytes, filename, mimetype)
        status = 400 if response.get("error") else 200
        return request.make_response(
            json.dumps(response),
            headers=[("Content-Type", "application/json")],
            status=status,
        )

    @http.route(
        "/realtime_agent/session_abandoned",
        type="jsonrpc",
        auth="public",
        website=True,
        csrf=False,
    )
    def realtime_agent_session_abandoned(self, **kwargs):
        """Persist a fallback summary when the visitor leaves before audio upload completes."""
        return self._save_abandoned_summary(kwargs)

    @http.route("/realtime_agent/sip/recording", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_sip_recording(self, **kwargs):
        """SIP recording ingestion is disabled; widget-only mode."""
        return {
            "error": "Disabled route",
            "details": "SIP/VoIP recording ingestion is disabled. Use the Realtime Agent Widget.",
        }

    @http.route("/realtime_agent/voip/recording", type="jsonrpc", auth="user", website=False, csrf=False)
    def realtime_agent_voip_recording(self, **kwargs):
        """Standard Odoo VoIP recording ingestion is disabled; widget-only mode."""
        return {
            "error": "Disabled route",
            "details": "Standard Odoo VoIP call recording is disabled. Use the Realtime Agent Widget.",
        }

    @http.route("/realtime_agent/recording/<int:summary_id>", type="http", auth="user", website=False)
    def realtime_agent_recording(self, summary_id):
        record = request.env["crm.realtime_call_summary"].browse(summary_id)
        try:
            record.check_access("read")
        except AccessError:
            return request.not_found()
        if not record or not record.attachment_id:
            return request.not_found()
        attachment = record.attachment_id
        try:
            content = base64.b64decode(attachment.datas or b"")
        except Exception:
            return request.not_found()
        mimetype = attachment.mimetype or "application/octet-stream"
        filename = attachment.name or "realtime-call.webm"
        headers = [
            ("Content-Type", mimetype),
            ("Content-Disposition", f'inline; filename="{filename}"'),
            ("Content-Security-Policy", "default-src 'self'; media-src 'self'"),
        ]
        return request.make_response(content, headers=headers)

    @http.route("/realtime_agent/recording_player/<int:summary_id>", type="http", auth="user", website=False)
    def realtime_agent_recording_player(self, summary_id):
        record = request.env["crm.realtime_call_summary"].browse(summary_id)
        try:
            record.check_access("read")
        except AccessError:
            return request.not_found()
        if not record or not record.attachment_id:
            return request.not_found()

        filename = escape(record.attachment_id.name or "realtime-call.webm")
        media_url = f"/realtime_agent/recording/{record.id}"
        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{filename}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f7f8fa; color: #1f2937; }}
    .card {{ max-width: 680px; margin: 0 auto; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; }}
    h1 {{ margin: 0 0 12px; font-size: 18px; }}
    p {{ margin: 0 0 14px; color: #4b5563; font-size: 14px; }}
    audio {{ width: 100%; }}
    a {{ color: #0b6bcb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{filename}</h1>
    <p>Click play to listen to the recording.</p>
    <audio controls preload="metadata" src="{media_url}"></audio>
    <p><a href="{media_url}" target="_blank">Open raw media</a></p>
  </div>
</body>
</html>"""
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Security-Policy", "default-src 'self'; media-src 'self'; style-src 'unsafe-inline'"),
        ]
        return request.make_response(html, headers=headers)
