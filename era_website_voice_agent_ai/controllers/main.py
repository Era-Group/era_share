# -*- coding: utf-8 -*-
import base64
import io
import json
from html import escape
import os
import re
import tempfile
import uuid
from datetime import timedelta
from urllib.parse import urlparse
import requests

from odoo import fields, http
from odoo.exceptions import AccessError
from odoo.http import request


class RealtimeAgentController(http.Controller):
    MAX_AUDIO_BYTES = 40 * 1024 * 1024
    MAX_TRANSCRIBE_AUDIO_BYTES = 24 * 1024 * 1024

    def _get_realtime_model(self, ICP):
        """Return the configured model or the default gpt-realtime-mini."""
        configured_model = ICP.get_param("openai.realtime_model")
        return configured_model or "gpt-realtime-mini"

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
        def _or_domain(conditions):
            if not conditions:
                return []
            if len(conditions) == 1:
                return list(conditions)
            return (["|"] * (len(conditions) - 1)) + list(conditions)

        if phone:
            phone_conditions = []
            if "phone" in Lead._fields:
                phone_conditions.append(("phone", "ilike", phone))
            if "mobile" in Lead._fields:
                phone_conditions.append(("mobile", "ilike", phone))
            phone_domain = _or_domain(phone_conditions)
            if phone_domain:
                lead = Lead.search(
                    phone_domain,
                    order="write_date desc, create_date desc",
                    limit=1,
                )
        if not lead and company:
            company_conditions = []
            if "partner_name" in Lead._fields:
                company_conditions.append(("partner_name", "ilike", company))
            if "name" in Lead._fields:
                company_conditions.append(("name", "ilike", company))
            company_domain = _or_domain(company_conditions)
            if company_domain:
                lead = Lead.search(
                    company_domain,
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
        text = self._extract_transcription_text(payload)
        if not text:
            # Empty transcript is possible for silence/non-speech audio; treat as non-fatal.
            return "", None
        return text, None

    def _extract_transcription_text(self, payload):
        text = (payload.get("text") or "").strip()
        if text:
            return text
        transcript = (payload.get("transcript") or "").strip()
        if transcript:
            return transcript
        output_text = (payload.get("output_text") or "").strip()
        if output_text:
            return output_text
        for segment in payload.get("segments", []) or []:
            seg_text = (segment.get("text") or "").strip()
            if seg_text:
                return seg_text
        return ""

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

    def _normalize_client_call_id(self, value):
        client_call_id = (value or "").strip().lower()
        if re.fullmatch(r"[a-z0-9_-]{8,128}", client_call_id):
            return client_call_id
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

    def _chunk_file_size(self, session_key):
        path = self._chunk_file_path(session_key)
        if not path or not os.path.exists(path):
            return 0
        try:
            return os.path.getsize(path)
        except Exception:
            return 0

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

    def _configured_salesperson_user_id(self, ICP=None):
        ICP = ICP or request.env["ir.config_parameter"].sudo()
        raw_user_id = (ICP.get_param("openai.realtime_salesperson_user_id") or "").strip()
        try:
            user_id = int(raw_user_id or 0)
        except Exception:
            user_id = 0
        if not user_id:
            return 0
        user = request.env["res.users"].sudo().browse(user_id).exists()
        if not user or user.share:
            return 0
        return user.id

    def _request_ip(self):
        headers = request.httprequest.headers
        forwarded_for = (headers.get("X-Forwarded-For") or "").strip()
        if forwarded_for:
            # Use the left-most value as original client IP.
            return forwarded_for.split(",")[0].strip()
        real_ip = (headers.get("X-Real-IP") or "").strip()
        if real_ip:
            return real_ip
        return (request.httprequest.remote_addr or "").strip()

    def _caller_ip_for_payload(self, payload=None):
        if self._is_embed_request(payload):
            return self._request_ip()
        return ""

    def _is_truthy(self, value):
        return str(value or "").lower() in ("1", "true", "yes", "y", "t")

    def _is_external_embed_enabled(self, ICP):
        raw_embed_enabled = ICP.get_param("openai.realtime_embed_enabled")
        if raw_embed_enabled in (None, ""):
            # Backward compatibility: before split settings, external embed followed the legacy widget flag.
            raw_embed_enabled = ICP.get_param("openai.realtime_widget_enabled", "1")
        return self._is_truthy(raw_embed_enabled or "1")

    def _is_embed_request(self, payload=None):
        payload = payload or {}
        if self._is_truthy(payload.get("embed_mode")):
            return True
        referer = (request.httprequest.headers.get("Referer") or "").strip()
        if not referer:
            return False
        try:
            path = urlparse(referer).path or ""
        except Exception:
            return False
        return path.startswith("/realtime_agent/embed/frame")

    def _guard_external_embed_enabled_json(self, payload=None):
        if not self._is_embed_request(payload):
            return None
        ICP = request.env["ir.config_parameter"].sudo()
        if self._is_external_embed_enabled(ICP):
            return None
        return {"error": "External embed widget is disabled."}

    def _guard_external_embed_enabled_http(self, payload=None):
        if not self._is_embed_request(payload):
            return None
        ICP = request.env["ir.config_parameter"].sudo()
        if self._is_external_embed_enabled(ICP):
            return None
        return request.make_response(
            json.dumps({"error": "External embed widget is disabled."}),
            headers=[("Content-Type", "application/json")],
            status=403,
        )

    def _guard_single_active_ip_session_json(self, payload=None):
        payload = payload or {}
        if not self._is_embed_request(payload):
            return None
        caller_ip = self._request_ip()
        if not caller_ip:
            return None
        active = self._find_recent_open_summary(caller_ip=caller_ip, max_age_minutes=20)
        if not active:
            return None

        incoming_client_call_id = self._normalize_client_call_id(payload.get("client_call_id"))
        active_client_call_id = self._normalize_client_call_id(active.client_call_id)
        if active_client_call_id and incoming_client_call_id and incoming_client_call_id == active_client_call_id:
            return None
        # If active call id is not yet set (older records), allow only exact same summary+session.
        if not active_client_call_id:
            incoming_summary_id = payload.get("summary_id")
            try:
                incoming_summary_id = int(incoming_summary_id or 0)
            except Exception:
                incoming_summary_id = 0
            incoming_session_key = self._normalize_session_key(payload.get("session_key"))
            if incoming_summary_id and incoming_summary_id == active.id:
                if incoming_session_key and active.session_key and active.session_key == incoming_session_key:
                    return None

        return {"error": "يوجد وكيل نشط بالفعل من نفس عنوان IP. أغلق الجلسة الحالية أولاً."}

    def _find_recent_open_summary(
        self,
        caller_phone="",
        caller_company="",
        model="",
        voice="",
        caller_ip="",
        max_age_minutes=120,
    ):
        Summary = self._summary_model()
        phone = (caller_phone or "").strip()
        ip = (caller_ip or "").strip()
        if not phone and not ip:
            return Summary.browse()
        domain = [
            ("call_source", "=", "agent"),
        ]
        if phone:
            domain.append(("caller_phone", "=", phone))
        if ip and "caller_ip" in Summary._fields:
            domain.append(("caller_ip", "=", ip))
        if caller_company and "caller_company" in Summary._fields:
            domain.append(("caller_company", "=", caller_company))
        if model and "model" in Summary._fields:
            domain.append(("model", "=", model))
        if voice and "voice" in Summary._fields:
            domain.append(("voice", "=", voice))
        if "is_active" in Summary._fields:
            domain.append(("is_active", "=", True))
        elif "attachment_id" in Summary._fields:
            domain.append(("attachment_id", "=", False))
        cutoff = fields.Datetime.now() - timedelta(minutes=max_age_minutes)
        domain.append(("create_date", ">=", cutoff))
        return Summary.search(domain, order="id desc", limit=1)

    def _parse_allowed_embed_origins(self, ICP):
        raw = (ICP.get_param("openai.realtime_embed_allowed_origins") or "").strip()
        if not raw:
            return set()
        items = []
        for part in raw.replace(",", "\n").splitlines():
            value = part.strip()
            if value:
                items.append(value)
        origins = set()
        for item in items:
            parsed = urlparse(item)
            if parsed.scheme and parsed.netloc:
                origins.add(f"{parsed.scheme}://{parsed.netloc}".lower())
        return origins

    def _request_origin(self):
        headers = request.httprequest.headers
        origin = (headers.get("Origin") or "").strip()
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".lower()
        referer = (headers.get("Referer") or "").strip()
        if referer:
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".lower()
        return ""

    def _get_session_record(self, kwargs):
        Summary = self._summary_model()
        summary_id = kwargs.get("summary_id")
        session_key = self._normalize_session_key(kwargs.get("session_key"))
        client_call_id = self._normalize_client_call_id(kwargs.get("client_call_id"))

        if summary_id:
            try:
                summary_id = int(summary_id)
            except Exception:
                summary_id = 0
            if summary_id:
                record = Summary.browse(summary_id).exists()
                if record:
                    if "session_key" not in Summary._fields:
                        return record
                    # Accept direct id match when no session key was provided.
                    if not session_key:
                        return record
                    if record.session_key == session_key:
                        return record

        # Fallback: locate by session_key to avoid duplicate records if summary_id is missing/mismatched.
        if session_key and "session_key" in Summary._fields:
            return Summary.search([("session_key", "=", session_key)], order="id desc", limit=1)
        if client_call_id and "client_call_id" in Summary._fields:
            return Summary.search([("client_call_id", "=", client_call_id)], order="id desc", limit=1)
        return Summary.browse()

    def _upsert_summary_record(self, kwargs, values):
        Summary = self._summary_model()
        configured_salesperson_id = self._configured_salesperson_user_id()
        if (
            "salesperson_user_id" in Summary._fields
            and configured_salesperson_id
            and not values.get("salesperson_user_id")
        ):
            values["salesperson_user_id"] = configured_salesperson_id
        values = {k: v for k, v in values.items() if k in Summary._fields}
        record = self._get_session_record(kwargs)
        if record:
            record.write(values)
            return record
        session_key = self._normalize_session_key(kwargs.get("session_key"))
        client_call_id = self._normalize_client_call_id(kwargs.get("client_call_id"))
        caller_ip = (values.get("caller_ip") or "").strip()
        if caller_ip:
            recent = self._find_recent_open_summary(
                caller_ip=caller_ip,
                max_age_minutes=20,
            )
        else:
            recent = self._find_recent_open_summary(
                caller_phone=values.get("caller_phone") or "",
                caller_company=values.get("caller_company") or "",
                model=values.get("model") or "",
                voice=values.get("voice") or "",
            )
        if recent:
            patch_vals = dict(values)
            if "session_key" in Summary._fields and session_key and not recent.session_key:
                patch_vals["session_key"] = session_key
            if "client_call_id" in Summary._fields and client_call_id and not recent.client_call_id:
                patch_vals["client_call_id"] = client_call_id
            recent.write(patch_vals)
            return recent
        if "session_key" in Summary._fields:
            if not session_key and not client_call_id:
                return Summary.browse()
            if session_key:
                values["session_key"] = session_key
        if "client_call_id" in Summary._fields and client_call_id:
            values["client_call_id"] = client_call_id
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
            "caller_ip": self._caller_ip_for_payload(kwargs),
            "is_active": False,
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        record = self._upsert_summary_record(kwargs, values)
        if not record:
            return {"error": "Invalid session"}
        attachment_values = {
            "name": filename,
            "datas": base64.b64encode(audio_bytes),
            "mimetype": mimetype,
            "res_model": "crm.realtime_call_summary",
            "res_id": record.id,
        }
        if record.attachment_id:
            record.attachment_id.sudo().write(attachment_values)
            attachment = record.attachment_id
        else:
            attachment = request.env["ir.attachment"].sudo().create(attachment_values)
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
            if "is_active" in existing._fields and existing.is_active:
                existing.sudo().write({"is_active": False})
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
            "caller_ip": self._caller_ip_for_payload(kwargs),
            "is_active": False,
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        record = self._upsert_summary_record(kwargs, values)
        if not record:
            return {"error": "Invalid session"}
        return {"id": record.id, "summary": values.get("summary")}

    def _end_active_session(self, kwargs):
        Summary = self._summary_model()
        record = self._get_session_record(kwargs)
        if not record:
            client_call_id = self._normalize_client_call_id(kwargs.get("client_call_id"))
            if client_call_id and "client_call_id" in Summary._fields:
                record = Summary.search([("client_call_id", "=", client_call_id)], order="id desc", limit=1)
        if not record:
            caller_ip = self._caller_ip_for_payload(kwargs)
            if caller_ip and "caller_ip" in Summary._fields:
                record = self._find_recent_open_summary(caller_ip=caller_ip, max_age_minutes=20)
        if not record:
            return {"ok": False}

        has_attachment = bool(
            "attachment_id" in record._fields and getattr(record, "attachment_id", False)
        )
        chunk_audio = b""
        if not has_attachment:
            chunk_audio = self._read_chunk_file(kwargs.get("session_key"))
        if chunk_audio:
            payload = dict(kwargs or {})
            payload.setdefault("summary_id", record.id)
            if "session_key" in record._fields:
                payload.setdefault("session_key", record.session_key or "")
            if "client_call_id" in record._fields:
                payload.setdefault("client_call_id", record.client_call_id or "")
            if "prompt_id" in record._fields:
                payload.setdefault("prompt_id", record.prompt_id or "")
            if "model" in record._fields:
                payload.setdefault("model", record.model or "")
            if "voice" in record._fields:
                payload.setdefault("voice", record.voice or "")
            if "caller_phone" in record._fields:
                payload.setdefault("caller_phone", record.caller_phone or "")
            if "caller_company" in record._fields:
                payload.setdefault("caller_company", record.caller_company or "")
            audio_result = self._save_summary_audio(
                payload,
                chunk_audio,
                kwargs.get("audio_filename") or "realtime-call.webm",
                kwargs.get("audio_mimetype") or "audio/webm",
            )
            if audio_result.get("error"):
                # Fall back to closing the active session even if audio finalize fails.
                pass
            else:
                response = {"ok": True, "id": audio_result.get("id") or record.id}
                if audio_result.get("attachment_id"):
                    response["attachment_id"] = audio_result.get("attachment_id")
                if audio_result.get("warning"):
                    response["warning"] = audio_result.get("warning")
                return response

        vals = {}
        if "is_active" in record._fields and record.is_active:
            vals["is_active"] = False
        duration = self._coerce_duration_seconds(kwargs.get("duration_seconds"))
        if duration is not None and "duration_seconds" in record._fields:
            current = record.duration_seconds or 0
            if duration > current:
                vals["duration_seconds"] = duration
        if vals:
            record.sudo().write(vals)
        return {"ok": True, "id": record.id}

    def _prompt_content_to_text(self, content):
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, str):
                    text = part.strip()
                    if text:
                        chunks.append(text)
                    continue
                if not isinstance(part, dict):
                    continue
                raw_text = part.get("text")
                if isinstance(raw_text, dict):
                    raw_text = raw_text.get("value") or raw_text.get("text") or ""
                text = str(raw_text or part.get("value") or "").strip()
                if text:
                    chunks.append(text)
            return "\n".join(chunks).strip()
        if isinstance(content, dict):
            raw_text = content.get("text")
            if isinstance(raw_text, dict):
                raw_text = raw_text.get("value") or raw_text.get("text") or ""
            text = str(raw_text or content.get("value") or "").strip()
            if text:
                return text
        return ""

    def _extract_prompt_instructions(self, payload):
        if not isinstance(payload, dict):
            return ""
        prompt_data = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else payload
        raw_instructions = prompt_data.get("instructions") or payload.get("instructions") or ""
        instructions = self._prompt_content_to_text(raw_instructions)
        if instructions:
            return instructions

        messages = prompt_data.get("messages") or payload.get("messages") or []
        if not isinstance(messages, list):
            return ""

        collected = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or "").strip().lower()
            if role not in ("system", "developer"):
                continue
            text = self._prompt_content_to_text(msg.get("content"))
            if text:
                collected.append(text)
        return "\n\n".join(collected).strip()

    def _fetch_prompt_profile(self, api_key, prompt_id):
        """Read prompt metadata from OpenAI without sending prompt reference to Realtime sessions."""
        if not prompt_id:
            return {"has_mcp_tools": False, "instructions": ""}, None
        url = f"https://api.openai.com/v1/prompts/{prompt_id}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "prompts=v1",
        }
        try:
            r = requests.get(url, headers=headers, timeout=(3.05, 8))
        except Exception as e:
            return None, f"OpenAI prompt lookup failed: {e}"
        if r.status_code in (401, 403):
            return None, f"OpenAI prompt lookup unauthorized: {r.status_code} {r.text}"
        if r.status_code >= 400:
            return None, f"OpenAI prompt lookup failed: {r.status_code} {r.text}"
        try:
            data = r.json()
        except Exception as e:
            return None, f"OpenAI prompt lookup invalid JSON: {e}"
        tools = data.get("tools") or (data.get("prompt") or {}).get("tools") or []
        has_mcp_tools = False
        for tool in tools:
            tool_type = (tool.get("type") or tool.get("tool_type") or "").lower()
            provider = (tool.get("provider") or "").lower()
            if tool_type == "mcp" or provider == "mcp":
                has_mcp_tools = True
                break
        instructions = self._extract_prompt_instructions(data)
        return {"has_mcp_tools": has_mcp_tools, "instructions": instructions}, None

    @http.route("/realtime_agent/token", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_token(self, **kwargs):
        """Return a short-lived token for browser clients to connect to the Realtime API."""
        blocked = self._guard_external_embed_enabled_json(kwargs)
        if blocked:
            return blocked
        blocked = self._guard_single_active_ip_session_json(kwargs)
        if blocked:
            return blocked

        ICP = request.env["ir.config_parameter"].sudo()
        api_key = ICP.get_param("openai.api_key")
        
        model = self._get_realtime_model(ICP)
        raw_voice = ICP.get_param("openai.realtime_voice")
        voice = raw_voice or "alloy"
        if voice == "marin":
            voice = "alloy"
        interrupt_response_enabled = self._is_truthy(
            ICP.get_param("openai.realtime_interrupt_response_enabled", "0")
        )
        idle_timeout_seconds = 0
        try:
            idle_timeout_seconds = int(
                float(ICP.get_param("openai.realtime_idle_timeout_seconds", "0") or 0)
            )
        except Exception:
            idle_timeout_seconds = 0
        if idle_timeout_seconds < 0:
            idle_timeout_seconds = 0
        system_instructions = (ICP.get_param("openai.realtime_system_instructions") or "").strip()
        prompt_id = (ICP.get_param("openai.realtime_prompt_id") or "").strip()
        if not api_key:
            return {"error": "Missing system parameter: openai.api_key"}

        prompt_profile = {"has_mcp_tools": False, "instructions": ""}
        prompt_warning = ""
        if not system_instructions:
            try:
                fetched_profile, prompt_error = self._fetch_prompt_profile(api_key, prompt_id)
                if prompt_error:
                    prompt_warning = prompt_error
                elif fetched_profile:
                    prompt_profile = fetched_profile
            except Exception as e:
                prompt_warning = f"Prompt lookup failed: {e}"

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
            "turn_detection": {
                "type": "server_vad",
                "interrupt_response": interrupt_response_enabled,
            },
        }
        if idle_timeout_seconds > 0:
            payload["turn_detection"]["idle_timeout_ms"] = idle_timeout_seconds * 1000
        if system_instructions:
            payload["instructions"] = system_instructions
        else:
            prompt_instructions = ""
            if prompt_profile:
                prompt_instructions = (prompt_profile.get("instructions") or "").strip()
            if prompt_instructions:
                payload["instructions"] = prompt_instructions

        def _is_idle_timeout_not_supported(response_text):
            text = (response_text or "").lower()
            return "idle_timeout_ms" in text or "idle timeout" in text

        def _mint_session(session_payload):
            try:
                return requests.post(url, headers=headers, data=json.dumps(session_payload), timeout=20), None
            except Exception as e:
                return None, str(e)

        r, request_error = _mint_session(payload)
        if request_error:
            return {"error": f"OpenAI request failed: {request_error}"}

        if (
            r.status_code >= 400
            and idle_timeout_seconds > 0
            and _is_idle_timeout_not_supported(r.text)
        ):
            fallback_payload = dict(payload)
            turn_detection = dict(fallback_payload.get("turn_detection") or {})
            turn_detection.pop("idle_timeout_ms", None)
            fallback_payload["turn_detection"] = turn_detection
            fallback_resp, fallback_error = _mint_session(fallback_payload)
            if fallback_error:
                return {"error": f"OpenAI request failed: {fallback_error}"}
            r = fallback_resp

        if r.status_code >= 400:
            return {"error": "OpenAI token mint failed", "status": r.status_code, "details": r.text}

        data = r.json()
        client_secret = (data.get("client_secret") or {}).get("value")
        if not client_secret:
            return {"error": "No client_secret returned by OpenAI", "details": data}

        response = {
            "value": client_secret,
            "prompt_fallback": False,
            "interrupt_response_enabled": interrupt_response_enabled,
            "idle_timeout_seconds": idle_timeout_seconds,
        }
        if prompt_warning:
            response["prompt_lookup_warning"] = prompt_warning
        return response

    @http.route("/realtime_agent/embed/frame", type="http", auth="public", website=True, csrf=False)
    def realtime_agent_embed_frame(self, **kwargs):
        """Render a standalone widget frame for third-party website embedding."""
        ICP = request.env["ir.config_parameter"].sudo()
        if not self._is_external_embed_enabled(ICP):
            return request.make_response("")

        allowed_origins = self._parse_allowed_embed_origins(ICP)
        if not allowed_origins:
            return request.make_response("Embed is disabled until allowed origins are configured.", status=403)
        req_origin = self._request_origin()
        if not req_origin or req_origin not in allowed_origins:
            return request.make_response("Embed origin is not allowed.", status=403)

        def _pick(query_key, param_key, fallback=""):
            value = (kwargs.get(query_key) or "").strip()
            if value:
                return value
            return (ICP.get_param(param_key) or fallback).strip()

        values = {
            "prompt_id": _pick("prompt_id", "openai.realtime_prompt_id"),
            "model": _pick("model", "openai.realtime_model", "gpt-realtime-mini"),
            "voice": _pick("voice", "openai.realtime_voice", "alloy"),
            "widget_label": _pick("widget_label", "openai.realtime_widget_label"),
            "caller_phone": (kwargs.get("caller_phone") or "").strip(),
            "caller_company": (kwargs.get("caller_company") or "").strip(),
        }
        response = request.render("era_website_voice_agent_ai.realtime_agent_embed_frame_page", values)
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        return response

    @http.route("/realtime_agent/session_start", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_session_start(self, **kwargs):
        blocked = self._guard_external_embed_enabled_json(kwargs)
        if blocked:
            return blocked

        ICP = request.env["ir.config_parameter"].sudo()
        Summary = self._summary_model()
        client_call_id = self._normalize_client_call_id(kwargs.get("client_call_id"))
        caller_phone = (kwargs.get("caller_phone") or "").strip()
        caller_company = (kwargs.get("caller_company") or "").strip()
        caller_ip = self._caller_ip_for_payload(kwargs)
        model = kwargs.get("model") or ICP.get_param("openai.realtime_model")
        voice = kwargs.get("voice") or ICP.get_param("openai.realtime_voice")
        configured_salesperson_id = self._configured_salesperson_user_id(ICP)

        if client_call_id and "client_call_id" in Summary._fields:
            existing = Summary.search([("client_call_id", "=", client_call_id)], order="id desc", limit=1)
            if existing:
                existing_key = existing.session_key or ""
                write_vals = {}
                if not existing_key and "session_key" in Summary._fields:
                    existing_key = uuid.uuid4().hex
                    write_vals["session_key"] = existing_key
                if caller_ip and "caller_ip" in Summary._fields and not existing.caller_ip:
                    write_vals["caller_ip"] = caller_ip
                if "is_active" in Summary._fields and not existing.is_active:
                    write_vals["is_active"] = True
                if (
                    "salesperson_user_id" in Summary._fields
                    and configured_salesperson_id
                    and not existing.salesperson_user_id
                ):
                    write_vals["salesperson_user_id"] = configured_salesperson_id
                if write_vals:
                    existing.sudo().write(write_vals)
                return {"summary_id": existing.id, "session_key": existing_key}

        if caller_ip:
            existing = self._find_recent_open_summary(
                caller_ip=caller_ip,
                max_age_minutes=20,
            )
        else:
            existing = self._find_recent_open_summary(
                caller_phone=caller_phone,
                caller_company=caller_company,
                model=model or "",
                voice=voice or "",
                max_age_minutes=20,
            )
        if existing:
            active_client_call_id = self._normalize_client_call_id(existing.client_call_id)
            if active_client_call_id and client_call_id and active_client_call_id != client_call_id:
                return {"error": "يوجد وكيل نشط بالفعل من نفس عنوان IP. أغلق الجلسة الحالية أولاً."}
            if active_client_call_id and not client_call_id:
                return {"error": "يوجد وكيل نشط بالفعل من نفس عنوان IP. أغلق الجلسة الحالية أولاً."}
            existing_key = existing.session_key or ""
            write_vals = {}
            if "session_key" in Summary._fields and not existing_key:
                existing_key = uuid.uuid4().hex
                write_vals["session_key"] = existing_key
            if "client_call_id" in Summary._fields and client_call_id and not existing.client_call_id:
                write_vals["client_call_id"] = client_call_id
            if "caller_ip" in Summary._fields and caller_ip and not existing.caller_ip:
                write_vals["caller_ip"] = caller_ip
            if "is_active" in Summary._fields and not existing.is_active:
                write_vals["is_active"] = True
            if (
                "salesperson_user_id" in Summary._fields
                and configured_salesperson_id
                and not existing.salesperson_user_id
            ):
                write_vals["salesperson_user_id"] = configured_salesperson_id
            if write_vals:
                existing.sudo().write(write_vals)
            return {"summary_id": existing.id, "session_key": existing_key}

        session_key = uuid.uuid4().hex
        values = {
            "summary": "بدأت مكالمة من الموقع.",
            "prompt_id": kwargs.get("prompt_id") or ICP.get_param("openai.realtime_prompt_id"),
            "prompt_version": ICP.get_param("openai.realtime_prompt_version"),
            "model": model,
            "voice": voice,
            "duration_seconds": 0,
            "call_source": "agent",
            "caller_phone": caller_phone,
            "caller_company": caller_company,
            "caller_ip": caller_ip,
            "is_active": True,
            "salesperson_user_id": configured_salesperson_id,
        }
        lead = self._find_lead(phone=values["caller_phone"], company=values["caller_company"])
        if lead:
            values["lead_id"] = lead.id
        if "session_key" in Summary._fields:
            values["session_key"] = session_key
        if "client_call_id" in Summary._fields and client_call_id:
            values["client_call_id"] = client_call_id
        values = {k: v for k, v in values.items() if k in Summary._fields}
        record = Summary.create(values)
        return {"summary_id": record.id, "session_key": session_key}

    @http.route("/realtime_agent/chunk", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_chunk(self, **kwargs):
        blocked = self._guard_external_embed_enabled_json(kwargs)
        if blocked:
            return blocked

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
        current_size = self._chunk_file_size(kwargs.get("session_key"))
        if current_size + len(chunk_bytes) > self.MAX_AUDIO_BYTES:
            return {"error": "Audio too large"}
        if not self._append_chunk_file(kwargs.get("session_key"), chunk_bytes):
            return {"error": "Invalid session key"}
        return {"ok": True, "summary_id": record.id}

    @http.route("/realtime_agent/summary", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_summary(self, **kwargs):
        """Summarize the transcript and store it in CRM."""
        blocked = self._guard_external_embed_enabled_json(kwargs)
        if blocked:
            return blocked

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
            "caller_ip": self._caller_ip_for_payload(kwargs),
            "is_active": False,
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        record = self._upsert_summary_record(kwargs, values)
        if not record:
            return {"error": "Invalid session"}
        response = {"id": record.id, "summary": summary}
        if warning:
            response["warning"] = warning
        return response

    @http.route("/realtime_agent/summary_audio", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_summary_audio(self, **kwargs):
        """Store recording, transcribe it, and save summary in CRM."""
        blocked = self._guard_external_embed_enabled_json(kwargs)
        if blocked:
            return blocked

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
        blocked = self._guard_external_embed_enabled_http(post)
        if blocked:
            return blocked

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
        blocked = self._guard_external_embed_enabled_json(kwargs)
        if blocked:
            return blocked
        return self._save_abandoned_summary(kwargs)

    @http.route("/realtime_agent/session_end", type="jsonrpc", auth="public", website=True, csrf=False)
    def realtime_agent_session_end(self, **kwargs):
        return self._end_active_session(kwargs)

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
