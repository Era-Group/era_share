"""Route an AI agent's *generation* through an era.ai.account when one is set.

RAG/embeddings keep using the standard provider (derived from ``llm_model``), so
only the final model call is overridden. Also ports the resilient chat-channel
error handling from era_odoo_ai_ext (friendly errors, placeholder de-duplication,
tool-payload suppression).
"""
import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext, html_sanitize, is_html_empty

from odoo.addons.ai.models.ai_agent import TEMPERATURE_MAP, markdown
from odoo.addons.ai.utils.llm_api_service import LLMApiService

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _inherit = "ai.agent"

    era_account_id = fields.Many2one(
        "era.ai.account",
        string="AI Account",
        help="When set, this agent's responses are generated through the selected "
             "account (e.g. Claude via the local CLI proxy) instead of the global "
             "provider keys. Knowledge-source embeddings still use the standard "
             "provider from 'LLM Model'.",
    )
    era_model_id = fields.Many2one(
        "era.ai.model",
        string="Account Model",
        domain="[('account_id', '=', era_account_id), ('kind', '=', 'chat'), ('active', '=', True)]",
    )

    @api.onchange("era_account_id")
    def _onchange_era_account_id(self):
        if not self.era_account_id:
            self.era_model_id = False
            return
        if self.era_model_id and self.era_model_id.account_id != self.era_account_id:
            self.era_model_id = False
        if not self.era_model_id:
            self.era_model_id = self.era_account_id._default_chat_model_record().id

    @api.constrains("era_account_id", "era_model_id")
    def _check_era_model(self):
        for agent in self:
            if agent.era_model_id and agent.era_model_id.account_id != agent.era_account_id:
                raise ValidationError(_("The selected Account Model does not belong to the AI Account."))

    # ------------------------------------------------------------------ routing
    def _generate_response(self, prompt, chat_history=None, extra_system_context=""):
        if not self.era_account_id:
            return super()._generate_response(prompt, chat_history, extra_system_context)
        self.ensure_one()
        acc = self.era_account_id
        acc._assert_usable()
        model = self.era_model_id.model_id if self.era_model_id else acc._default_chat_model()

        system_messages = self._build_system_context(extra_system_context=extra_system_context)
        rag_context = self._build_rag_context(prompt)  # standard embeddings path
        if rag_context:
            system_messages.extend(rag_context)

        supports_tools = acc.auth_mode == "api_key" and acc.provider in ("openai", "google", "custom")
        service = LLMApiService(
            env=self.with_context(era_ai_account_id=acc.id).env,
            provider=acc._service_provider(),
        )
        llm_response = service.request_llm(
            model,
            system_messages,
            [],
            inputs=(chat_history or []) + [{"role": "user", "content": prompt}],
            tools=self.sudo().topic_ids.tool_ids._get_ai_tools() if supports_tools else None,
            temperature=TEMPERATURE_MAP[self.response_style],
        )
        if rag_context:
            llm_response = self._get_llm_response_with_sources(llm_response)
        return llm_response

    # ----------------------------------------- resilient chat-channel handling
    def _generate_response_for_channel(self, mail_message, channel):
        self.ensure_one()
        prompt, session_info_context = self._parse_user_message(mail_message)
        try:
            response = self.with_context(discuss_channel=channel)._generate_response(
                prompt=prompt,
                chat_history=[{"content": session_info_context, "role": "user"}] + self._retrieve_chat_history(channel),
                extra_system_context=self._build_extra_system_context(channel),
            )
        except Exception as error:  # noqa: BLE001
            _logger.exception("AI request failed for channel %s", channel.id)
            response = [self._friendly_ai_error_message(error)]
        for message in response or []:
            self._post_ai_response(channel, message)

    def _friendly_ai_error_message(self, error):
        message = (str(error) or "").strip()
        if message.startswith("AI request failed:"):
            message = message.replace("AI request failed:", "", 1).strip()
        message = " ".join(message.split())
        if len(message) > 500:
            message = f"{message[:500]}..."
        return message or self.env._("AI request failed due to an unexpected error. Please try again.")

    def _retry_status_message(self, channel):
        recent_messages = self.env["mail.message"].sudo().search(
            [("model", "=", "discuss.channel"), ("res_id", "=", channel.id)],
            order="id desc", limit=6,
        )
        consecutive = 0
        in_try_again = False
        for msg in recent_messages:
            if msg.author_id.id != self.partner_id.id:
                break
            text = (html2plaintext(msg.body or "") or "").strip().lower()
            if text == "working..":
                consecutive += 1
            elif text == "try again!":
                consecutive += 1
                in_try_again = True
            else:
                break
        return "Try again!" if in_try_again or consecutive >= 2 else "Working.."

    def _is_internal_tool_payload(self, message):
        if not isinstance(message, str):
            return False
        text = message.strip()
        if not text:
            return False
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else text
        lowered = text.lower()
        if re.match(r"^\s*toolcalls?\s*>", lowered):
            return True
        payload = re.sub(r"^\s*toolcalls?\s*>\s*", "", text, flags=re.IGNORECASE)
        payload = re.sub(r"\s*>+\s*$", "", payload)
        candidates = [text]
        if payload != text:
            candidates.append(payload)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(parsed, dict) and "arguments" in parsed and ("name" in parsed or "tool" in parsed):
                return True
            if isinstance(parsed, list) and parsed and all(isinstance(i, dict) for i in parsed):
                if any("arguments" in i and ("name" in i or "tool" in i) for i in parsed):
                    return True
        return False

    def _post_ai_response(self, channel, message):
        if message is None:
            message = self._retry_status_message(channel)
        if not isinstance(message, str):
            message = str(message)
        if not message.strip():
            message = self._retry_status_message(channel)
        if message.strip().lower() in {"this message has been removed", "message removed"}:
            message = self._retry_status_message(channel)
        elif self._is_internal_tool_payload(message):
            message = "Try again!"

        if markdown:
            formatted_message = html_sanitize(markdown(message, extras=["fenced-code-blocks", "tables", "strike"]))
        else:
            formatted_message = html_sanitize(message)
        if is_html_empty(formatted_message):
            formatted_message = html_sanitize(self._retry_status_message(channel))

        last_message = self.env["mail.message"].sudo().search(
            [("model", "=", "discuss.channel"), ("res_id", "=", channel.id)],
            order="id desc", limit=1,
        )
        new_plain = (html2plaintext(formatted_message or "") or "").strip()
        last_plain = (html2plaintext(last_message.body or "") or "").strip() if last_message else ""
        status_messages = {"Working..", "Try again!"}
        if last_message and last_message.author_id.id == self.partner_id.id:
            if last_plain == new_plain:
                return
            if new_plain in status_messages and last_plain in status_messages:
                last_message.write({"body": formatted_message})
                return

        channel.sudo().message_post(
            author_id=self.partner_id.id,
            body=formatted_message,
            message_type="comment",
            silent=True,
            subtype_xmlid="mail.mt_comment",
        )
