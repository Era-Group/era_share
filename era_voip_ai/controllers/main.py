# -*- coding: utf-8 -*-
import base64
import io
import json
import requests

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


class RealtimeAgentController(http.Controller):
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

    @http.route("/realtime_agent/token", type="json", auth="public", website=True, csrf=False)
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

    @http.route("/realtime_agent/summary", type="json", auth="public", website=True, csrf=False)
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
            "duration_seconds": kwargs.get("duration_seconds"),
            "call_source": "agent",
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        Summary = request.env["crm.realtime_call_summary"].sudo()
        values = {k: v for k, v in values.items() if k in Summary._fields}
        record = Summary.create(values)
        response = {"id": record.id, "summary": summary}
        if warning:
            response["warning"] = warning
        return response

    @http.route("/realtime_agent/summary_audio", type="json", auth="public", website=True, csrf=False)
    def realtime_agent_summary_audio(self, **kwargs):
        """Store recording, transcribe it, and save summary in CRM."""
        audio_base64 = (kwargs.get("audio_base64") or "").strip()
        if not audio_base64:
            return {"error": "Missing audio"}

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as e:
            return {"error": "Invalid audio data", "details": str(e)}

        if len(audio_bytes) > 12 * 1024 * 1024:
            return {"error": "Audio too large"}

        filename = kwargs.get("audio_filename") or "realtime-call.webm"
        mimetype = kwargs.get("audio_mimetype") or "audio/webm"

        ICP = request.env["ir.config_parameter"].sudo()
        api_key = ICP.get_param("openai.api_key")
        if not api_key:
            return {"error": "Missing system parameter: openai.api_key"}
        summary_prompt = ICP.get_param("openai.realtime_summary_prompt") or None

        fallback_transcript = (kwargs.get("transcript") or "").strip()
        transcript, error = self._transcribe_audio(api_key, filename, mimetype, audio_bytes)
        warnings = []
        transcription_error = None
        if error:
            transcription_error = error
            transcript = ""
            warnings.append(f"Transcription failed: {transcription_error}")
        if not transcript and fallback_transcript:
            transcript = fallback_transcript
            warnings.append("Using client transcript fallback.")

        summary, error = (None, None)
        if transcript:
            summary, error = self._summarize_transcript(api_key, transcript, summary_prompt)
        if error:
            summary = "تعذر تلخيص المكالمة تلقائيا."
            warnings.append(f"Summary failed: {error}")
        if not summary:
            summary = "تعذر تلخيص المكالمة تلقائيا."

        values = {
            "summary": summary,
            "transcription": transcript,
            "prompt_id": kwargs.get("prompt_id") or ICP.get_param("openai.realtime_prompt_id"),
            "prompt_version": ICP.get_param("openai.realtime_prompt_version"),
            "model": kwargs.get("model") or ICP.get_param("openai.realtime_model"),
            "voice": kwargs.get("voice") or ICP.get_param("openai.realtime_voice"),
            "duration_seconds": kwargs.get("duration_seconds"),
            "call_source": "agent",
        }
        phone = kwargs.get("caller_phone") or ""
        company = kwargs.get("caller_company") or ""
        values["caller_phone"] = phone
        values["caller_company"] = company
        lead = self._find_lead(phone=phone, company=company)
        if lead:
            values["lead_id"] = lead.id
        Summary = request.env["crm.realtime_call_summary"].sudo()
        values = {k: v for k, v in values.items() if k in Summary._fields}
        record = Summary.create(values)
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
        response = {"id": record.id, "summary": summary, "attachment_id": attachment.id}
        if warnings:
            response["warning"] = " | ".join(warnings)
        return response

    @http.route("/realtime_agent/sip/recording", type="json", auth="public", website=True, csrf=False)
    def realtime_agent_sip_recording(self, **kwargs):
        """SIP recording ingestion is disabled; widget-only mode."""
        return {
            "error": "Disabled route",
            "details": "SIP/VoIP recording ingestion is disabled. Use the Realtime Agent Widget.",
        }

    @http.route("/realtime_agent/voip/recording", type="json", auth="user", website=False, csrf=False)
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
            record.check_access_rights("read")
            record.check_access_rule("read")
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
