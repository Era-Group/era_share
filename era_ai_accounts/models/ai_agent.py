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

# Used when the configured transport cannot embed (CLI proxies are text-only).
DEFAULT_EMBEDDING_FALLBACK = "text-embedding-3-small"


class AIAgent(models.Model):
    _inherit = "ai.agent"

    era_account_id = fields.Many2one(
        "era.ai.account",
        string="AI Account",
        domain="[('provider', '!=', 'assemblyai')]",
        help="When set, this agent's responses are generated through the selected "
             "account (e.g. Claude or ChatGPT via the local CLI proxy) instead of "
             "the global provider keys. Knowledge-source embeddings still use the "
             "standard provider from 'LLM Model'. Transcription-only providers such "
             "as AssemblyAI cannot be assigned to a chat agent.",
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
            if agent.era_account_id.provider == "assemblyai":
                raise ValidationError(_(
                    "AssemblyAI is a transcription-only provider and cannot be "
                    "assigned to a chat agent. Select it only in a transcription "
                    "account setting."))
            if agent.era_model_id and agent.era_model_id.account_id != agent.era_account_id:
                raise ValidationError(_("The selected Account Model does not belong to the AI Account."))

    # --------------------------------------------------------------- embeddings
    def _get_embedding_model(self):
        """Pick an embedding model that can actually answer.

        Embeddings never travel through the account transport: both the indexing
        crons and ``_build_rag_context`` build their LLMApiService from the
        *provider* of the embedding model, with no ``era_ai_account_id`` in the
        context. So knowledge sources are always embedded with the global
        provider keys, whatever account drives the chat.

        Two cases need help. A CLI-proxy account is text-only — see
        ``era.ai.model._check_kind_for_cli_proxy`` — so it can never serve an
        embeddings call. And an operator may simply want RAG on a different
        provider than chat. Both are handled here rather than failing deep
        inside the cron, where the only symptom is sources stuck in
        ``processing``.
        """
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        if override := params.get_param("ai.embedding_model_override"):
            return override
        if self.era_account_id.auth_mode == "cli_proxy":
            return params.get_param(
                "ai.embedding_fallback_model", DEFAULT_EMBEDDING_FALLBACK)
        return super()._get_embedding_model()

    # ------------------------------------------------------------------ routing
    def _generate_response(self, prompt, chat_history=None, extra_system_context=""):
        context_account_id = self.env.context.get("era_ai_account_id")
        acc = (self.env["era.ai.account"].browse(context_account_id).exists()
               if context_account_id else self.era_account_id)
        if not acc:
            return super()._generate_response(prompt, chat_history, extra_system_context)
        self.ensure_one()
        acc._assert_usable()
        selected = self.era_model_id if self.era_model_id.account_id == acc else False
        model = selected.model_id if selected else acc._default_chat_model()

        system_messages = self._build_system_context(extra_system_context=extra_system_context)
        rag_context = self._build_rag_context(prompt)  # standard embeddings path
        if rag_context:
            system_messages.extend(rag_context)

        # api_key: native tool calling (OpenAI/Gemini/custom/Z.AI/Kimi all speak
        # the OpenAI tool-call format). cli_proxy: the JSON-envelope tool loop in
        # llm_service_patch (claude/codex/kimi, and Z.AI through the claude
        # binary), gated by the account's "Allow agent tools" switch.
        supports_tools = (
            (acc.auth_mode == "cli_proxy" and acc.cli_tools_enabled)
            or (acc.auth_mode == "api_key"
                and acc.provider in ("openai", "google", "custom", "zai", "kimi"))
        )
        service = LLMApiService(
            env=self.with_context(era_ai_account_id=acc.id).env,
            provider=acc._service_provider(),
        )
        # Usage was only ever counted by this module's own image, audio and
        # text helpers, never by the agent path — which is what every website
        # chat and every scheduled agent actually goes through. The accounts
        # doing all the work therefore reported zero requests and no error,
        # so a dead account and an unused one looked identical from the list.
        try:
            llm_response = service.request_llm(
                model,
                system_messages,
                [],
                inputs=(chat_history or []) + [{"role": "user", "content": prompt}],
                tools=self.sudo().topic_ids.tool_ids._get_ai_tools() if supports_tools else None,
                temperature=TEMPERATURE_MAP[self.response_style],
            )
        except Exception as error:
            acc.sudo()._log_failure(error)
            raise
        acc.sudo()._log_request()
        acc.sudo()._clear_failure()
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
            # Who is reading decides what they are told. An internal user is
            # debugging and wants the provider's own words; a website visitor
            # is a customer, and "The Codex CLI returned an empty response"
            # tells them nothing, looks broken, and is the last thing several
            # of them saw before leaving. Odoo's own handler makes this
            # distinction; overriding it here quietly removed it.
            if self.env.user._is_internal():
                response = [self._friendly_ai_error_message(error)]
            else:
                response = [self._visitor_error_message(channel)]
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

    # A language is only as good as the evidence for it. Browsers report
    # en_US for a great many people who do not read English — an Arabic
    # speaker on an English-locale phone is the common case, not the odd one —
    # so what the visitor actually typed outranks what their browser claimed.
    # Extend the map to teach it another script.
    SCRIPT_RANGES = {
        "ar": (("\u0600", "\u06ff"), ("\u0750", "\u077f")),
        "he": (("\u0590", "\u05ff"),),
        "ru": (("\u0400", "\u04ff"),),
        "el": (("\u0370", "\u03ff"),),
        "zh": (("\u4e00", "\u9fff"),),
        "ja": (("\u3040", "\u30ff"),),
        "ko": (("\uac00", "\ud7af"),),
        "th": (("\u0e00", "\u0e7f"),),
        "hi": (("\u0900", "\u097f"),),
    }

    def _language_from_script(self, channel):
        """The installed language whose script the visitor actually wrote in.

        Not language detection — a script check, which is all that is needed
        to tell Arabic from English and is right far more often than a browser
        header. Returns nothing when the text is plain Latin, because Latin
        script says nothing about which Latin language.
        """
        members = channel.sudo().channel_member_ids
        operators = members.mapped("partner_id").filtered(
            lambda partner: partner.user_ids.filtered(
                lambda user: not user.share))
        # The live chat operator is often a bot partner with no user behind
        # it, so the staff check alone does not catch it — and its own English
        # error messages then outweigh the customer's Arabic and decide the
        # language against them.
        if "livechat_operator_id" in channel._fields:
            operators |= channel.sudo().livechat_operator_id
        operators |= self.partner_id
        messages = self.env["mail.message"].sudo().search([
            ("model", "=", "discuss.channel"), ("res_id", "=", channel.id),
            ("message_type", "!=", "notification"),
        ], order="id desc", limit=30)
        text = " ".join(
            html2plaintext(message.body or "")
            for message in messages
            if message.author_id not in operators
            and message.author_id != self.partner_id
        )
        if not text.strip():
            return False
        installed = dict(self.env["res.lang"].get_installed())
        for prefix, ranges in self.SCRIPT_RANGES.items():
            hits = sum(1 for char in text
                       if any(low <= char <= high for low, high in ranges))
            # A stray character is a copied word; a fifth of the text is the
            # language they are writing in.
            if hits and hits > len([c for c in text if c.strip()]) / 5:
                for code in installed:
                    if code.split("_")[0] == prefix:
                        return code
        return False

    def _visitor_language(self, channel):
        """The language the person on the other side is actually reading."""
        written = self._language_from_script(channel)
        if written:
            return written
        members = channel.sudo().channel_member_ids
        # Guests first, and the two are looked at separately: they are
        # different models, so one recordset cannot simply be added to the
        # other.
        for guest in members.mapped("guest_id"):
            if guest.lang:
                return guest.lang
        for partner in members.mapped("partner_id"):
            if partner.lang and not partner.user_ids.filtered(
                    lambda user: not user.share):
                return partner.lang  # a customer's language, not an operator's
        return self.env.company.partner_id.lang or self.env.lang

    def _visitor_error_message(self, channel):
        """What a customer is told when our side fails.

        Says that it failed, apologises, and asks for a way to reach them —
        because the failure itself is not recoverable in the chat, but the
        customer is not lost as long as we can write back. Live chat visitors
        who leave an address are picked up from the transcript afterwards.
        """
        lang = self._visitor_language(channel)
        env = self.env(context=dict(self.env.context, lang=lang))
        return env._(
            "Sorry — something went wrong on our side and I could not answer "
            "just now. Please leave your email address or phone number here "
            "and someone from our team will get back to you, or try again in "
            "a few minutes."
        )

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
