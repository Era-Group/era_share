import logging
import json
import re

from odoo import models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext, html_sanitize, is_html_empty

from odoo.addons.ai.models.ai_agent import markdown

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _inherit = "ai.agent"

    def _build_rag_context(self, prompt):
        try:
            return super()._build_rag_context(prompt)
        except UserError as e:
            if "embedding" in str(e).lower():
                _logger.warning(
                    "RAG context skipped for agent %s: no valid embedding model configured. "
                    "Set 'ai.custom_llm_embedding_model' in system parameters. (%s)",
                    self.id, e,
                )
                return []
            raise

    def _retry_status_message(self, channel):
        """Return retry status text based on consecutive placeholder messages.

        1st/2nd placeholder: Working..
        3rd placeholder: Try again!
        """
        recent_messages = self.env["mail.message"].sudo().search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", channel.id),
            ],
            order="id desc",
            limit=6,
        )
        consecutive_placeholders = 0
        already_in_try_again = False
        for msg in recent_messages:
            if msg.author_id.id != self.partner_id.id:
                break
            text = (html2plaintext(msg.body or "") or "").strip().lower()
            if text == "working..":
                consecutive_placeholders += 1
            elif text == "try again!":
                consecutive_placeholders += 1
                already_in_try_again = True
            else:
                break

        # Once we reached "Try again!", keep it for subsequent placeholder posts
        # until a real AI response breaks the placeholder streak.
        return "Try again!" if already_in_try_again or consecutive_placeholders >= 2 else "Working.."

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

        # Some providers wrap function payloads like: TOOLCALL>[{...}]>
        toolcall_payload = re.sub(r"^\s*toolcalls?\s*>\s*", "", text, flags=re.IGNORECASE)
        toolcall_payload = re.sub(r"\s*>+\s*$", "", toolcall_payload)

        candidates = [text]
        if toolcall_payload != text:
            candidates.append(toolcall_payload)
        if '\\"' in text:
            candidates.append(text.replace('\\"', '"'))
        if '\\"' in toolcall_payload:
            candidates.append(toolcall_payload.replace('\\"', '"'))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:  # noqa: BLE001
                continue

            if isinstance(parsed, dict) and "arguments" in parsed and ("name" in parsed or "tool" in parsed):
                return True
            if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
                if any("arguments" in item and ("name" in item or "tool" in item) for item in parsed):
                    return True

        if lowered.startswith("{") and "arguments" in lowered and ("name" in lowered or "tool" in lowered):
            return True
        if lowered.startswith("[{") and "arguments" in lowered and ("name" in lowered or "tool" in lowered):
            return True
        lowered_toolcall_payload = toolcall_payload.lower()
        if lowered_toolcall_payload.startswith("{") and "arguments" in lowered_toolcall_payload and ("name" in lowered_toolcall_payload or "tool" in lowered_toolcall_payload):
            return True
        if lowered_toolcall_payload.startswith("[{") and "arguments" in lowered_toolcall_payload and ("name" in lowered_toolcall_payload or "tool" in lowered_toolcall_payload):
            return True
        return False

    def _friendly_ai_error_message(self, error):
        message = (str(error) or "").strip()
        if message.startswith("LLM provider request failed after") and ": " in message:
            message = message.split(": ", 1)[1].strip()

        if message.startswith("AI request failed:"):
            message = message.replace("AI request failed:", "", 1).strip()

        # Keep it readable in chat: one line, short, exact content.
        message = " ".join(message.split())
        if len(message) > 500:
            message = f"{message[:500]}..."

        if message:
            return message

        return self.env._("AI request failed due to an unexpected error. Please try again.")

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

    def _post_ai_response(self, channel, message):
        # Replace empty/removed artifacts with a friendly placeholder.
        if message is None:
            message = self._retry_status_message(channel)

        if not isinstance(message, str):
            message = str(message)

        if not message.strip():
            message = self._retry_status_message(channel)

        lowered = message.strip().lower()
        if lowered in {"this message has been removed", "message removed"}:
            message = self._retry_status_message(channel)
        elif self._is_internal_tool_payload(message):
            message = "Try again!"

        if markdown:
            raw_html = markdown(message, extras=["fenced-code-blocks", "tables", "strike"])
            formatted_message = html_sanitize(raw_html)
        else:
            formatted_message = html_sanitize(message)

        if is_html_empty(formatted_message):
            formatted_message = html_sanitize(self._retry_status_message(channel))

        # Avoid flooding chat with repeated placeholder/error messages.
        last_message = self.env["mail.message"].sudo().search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", channel.id),
            ],
            order="id desc",
            limit=1,
        )
        new_plain = (html2plaintext(formatted_message or "") or "").strip()
        last_plain = (html2plaintext(last_message.body or "") or "").strip() if last_message else ""
        status_messages = {"Working..", "Try again!"}

        if last_message and last_message.author_id.id == self.partner_id.id:
            if last_plain == new_plain:
                return
            if new_plain in status_messages and last_plain in status_messages:
                # Keep one status bubble and update its content instead of adding new bubbles.
                last_message.write({"body": formatted_message})
                return

        channel.sudo().message_post(
            author_id=self.partner_id.id,
            body=formatted_message,
            message_type="comment",
            silent=True,
            subtype_xmlid="mail.mt_comment",
        )
