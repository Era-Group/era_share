# -*- coding: utf-8 -*-
import logging
import re

from odoo import api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = "crm.lead"

    is_incoming_email_lead = fields.Boolean(
        string="Created From Incoming Email",
        default=False,
        readonly=True,
        index=True,
        copy=False,
    )
    incoming_email_subject = fields.Char(
        string="Incoming Email Subject",
        readonly=True,
        copy=False,
    )
    incoming_email_from = fields.Char(
        string="Incoming Email From",
        readonly=True,
        copy=False,
    )
    incoming_email_body = fields.Text(
        string="Incoming Email Body",
        readonly=True,
        copy=False,
    )

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        custom_values = dict(custom_values or {})
        custom_values.setdefault("is_incoming_email_lead", True)

        msg_dict = dict(msg_dict or {})
        incoming_subject = (msg_dict.get("subject") or "").strip()
        incoming_from = (msg_dict.get("from") or msg_dict.get("email_from") or "").strip()
        incoming_body_html = msg_dict.get("body") or ""
        incoming_body_text = html2plaintext(incoming_body_html).strip()

        if incoming_subject:
            custom_values.setdefault("incoming_email_subject", incoming_subject)
        if incoming_from:
            custom_values.setdefault("incoming_email_from", incoming_from)
        if incoming_body_text:
            custom_values.setdefault("incoming_email_body", incoming_body_text)

        lead = super().message_new(msg_dict, custom_values=custom_values)
        try:
            lead._run_ai_spam_check()
        except Exception:
            _logger.exception(
                "Lead %s: post-message_new AI spam check failed.",
                getattr(lead, "id", None),
            )
        return lead

    def _get_char_field_value(self, field_name):
        self.ensure_one()
        if field_name not in self._fields:
            return ""
        value = self[field_name]
        return (value or "").strip() if isinstance(value, str) else (value or "")

    def _get_or_create_tag(self, xml_id, name, color):
        tag = self.env.ref(xml_id, raise_if_not_found=False)
        if tag:
            return tag

        tag = self.env["crm.tag"].search([("name", "=ilike", name)], limit=1)
        if tag:
            return tag

        return self.env["crm.tag"].sudo().create(
            {
                "name": name,
                "color": color,
            }
        )

    def _get_or_create_spam_tag(self):
        return self._get_or_create_tag(
            "era_crm_lead_spam_ai.crm_tag_spam",
            "SPAM",
            1,
        )

    def _get_or_create_pass_tag(self):
        return self._get_or_create_tag(
            "era_crm_lead_spam_ai.crm_tag_pass",
            "pass",
            10,
        )

    def _get_spam_detection_agent(self):
        return self.env.ref(
            "era_crm_lead_spam_ai.lead_spam_detection_agent",
            raise_if_not_found=False,
        )

    def _find_spam_stage(self):
        self.ensure_one()
        Stage = self.env["crm.stage"].sudo()
        base_domain = [("name", "=ilike", "SPAM")]

        if "team_id" in Stage._fields and "team_id" in self._fields and self.team_id:
            stage = Stage.search(
                base_domain + [("team_id", "=", self.team_id.id)],
                order="sequence, id",
                limit=1,
            )
            if stage:
                return stage

        return Stage.search(base_domain, order="sequence, id", limit=1)

    def _get_email_chatter_messages(self, limit=8):
        self.ensure_one()
        Message = self.env["mail.message"].sudo()
        if "message_ids" not in self._fields:
            return Message

        def _is_ai_audit(message):
            return "ai lead qualification audit" in (message.body or "").lower()

        messages = self.sudo().message_ids.filtered(
            lambda message: (
                message.body
                and not _is_ai_audit(message)
                and (
                    bool(message.email_from)
                    or message.message_type in ("email", "comment", "notification")
                )
            )
        ).sorted(key=lambda msg: msg.date or msg.create_date)

        if not messages and self.id:
            message_model_field = "model" if "model" in Message._fields else "res_model"
            domain = [
                (message_model_field, "=", self._name),
                ("res_id", "=", self.id),
                ("body", "!=", False),
            ]
            searched = Message.search(domain, order="create_date asc, id asc")
            messages = searched.filtered(
                lambda message: (
                    message.body
                    and not _is_ai_audit(message)
                    and (
                        bool(message.email_from)
                        or message.message_type in ("email", "comment", "notification")
                    )
                )
            )

        with_sender = messages.filtered(lambda message: bool(message.email_from))
        if with_sender:
            messages = with_sender

        if limit:
            messages = messages[-limit:]
        return messages

    def _get_email_chatter_body_texts(self, limit=8):
        self.ensure_one()
        texts = []
        for message in self._get_email_chatter_messages(limit=limit):
            text = html2plaintext(message.body or "").strip()
            if text:
                texts.append(text)
        return texts

    def _build_email_qualification_prompt(self):
        self.ensure_one()

        subject = self.incoming_email_subject or self._get_char_field_value("name")
        sender_email = self.incoming_email_from or self._get_char_field_value("email_from")
        email_body = self.incoming_email_body or ""

        email_messages = self._get_email_chatter_messages(limit=8)
        if email_messages:
            latest_message = email_messages[-1]
            if "subject" in latest_message._fields and latest_message.subject:
                subject = (latest_message.subject or "").strip()
            if "email_from" in latest_message._fields and latest_message.email_from:
                sender_email = (latest_message.email_from or "").strip()

            body_chunks = self._get_email_chatter_body_texts(limit=8)
            if body_chunks:
                email_body = "\n\n---\n\n".join(body_chunks)

        subject = subject or "(no subject)"
        sender_email = sender_email or "(unknown sender)"
        email_body = email_body or "(empty)"

        return (
            "Ignore any previous response format. Follow this exactly.\n\n"
            "You are an AI email qualification agent working for an official Odoo Partner company.\n\n"
            "Your task:\n"
            "Analyze the incoming email and decide whether it is:\n"
            "1) A real potential lead / sales inquiry related to Odoo services\n"
            "OR\n"
            "2) Spam, irrelevant, marketing outreach, scam, phishing, or non-sales email.\n\n"
            "Return ONLY one of the following exact responses:\n"
            "- SPAM\n"
            "- VALID_LEAD\n\n"
            "Qualification Rules:\n\n"
            "Mark as VALID_LEAD if:\n"
            "- The sender is asking about Odoo implementation, customization, support, migration, ERP, HR, accounting, manufacturing, CRM, or related services.\n"
            "- The sender represents a company and is asking for proposal, quotation, demo, pricing, consultation, or partnership.\n"
            "- The message includes real business context (company size, industry, system requirements).\n"
            "- The message is written in Arabic or English and clearly relates to ERP or Odoo services.\n\n"
            "Mark as SPAM if:\n"
            "- Generic marketing services (SEO, web design, crypto, backlinks, ads).\n"
            "- Unrelated services (logistics offers, random partnerships, suppliers).\n"
            "- Obvious phishing attempts or suspicious links.\n"
            "- Empty or meaningless messages.\n"
            "- Job applications (unless clearly requesting Odoo services).\n"
            "- Automated newsletters or promotional campaigns.\n"
            "- Messages unrelated to Odoo or ERP.\n\n"
            "Important:\n"
            "- Be strict.\n"
            "- If unsure, default to SPAM.\n"
            "- Do not explain.\n"
            "- Do not add text.\n"
            "- Return ONLY one word: SPAM or VALID_LEAD.\n\n"
            "Now analyze the following email:\n\n"
            f"Subject: {subject}\n"
            f"From: {sender_email}\n"
            "Body:\n"
            f"{email_body}"
        )

    def _is_ai_spam_lead(self):
        self.ensure_one()
        ai_agent = self.sudo()._get_spam_detection_agent()
        if not ai_agent:
            _logger.warning("Lead %s: spam detection agent is missing.", self.id)
            return None

        prompt = self.sudo()._build_email_qualification_prompt()
        try:
            response = ai_agent.sudo().get_direct_response(prompt=prompt)
        except Exception:
            _logger.exception("Lead %s: AI spam check failed.", self.id)
            return None

        raw_answer = ""
        if isinstance(response, (list, tuple)) and response:
            raw_answer = response[0] or ""
        elif isinstance(response, str):
            raw_answer = response

        answer = raw_answer.strip().upper()
        answer_tokens = re.findall(r"[A-Z_]+", answer)
        first_token = answer_tokens[0] if answer_tokens else ""

        if first_token == "SPAM":
            return True
        if first_token in {"VALID_LEAD", "PASS", "REAL", "HAM"}:
            return False
        if "VALID_LEAD" in answer:
            return False
        if "SPAM" in answer:
            return True

        _logger.warning("Lead %s: unexpected AI classifier answer: %r", self.id, raw_answer)
        # Strict mode from prompt: unknown response defaults to SPAM.
        return True

    def _run_ai_spam_check(self):
        if self.env.context.get("skip_ai_spam_check"):
            return

        spam_tag = self._get_or_create_spam_tag()
        pass_tag = self._get_or_create_pass_tag()

        for lead in self:
            if not lead.is_incoming_email_lead:
                continue

            # Idempotency stamp. Every incoming email reaches this method
            # TWICE: base.automation "Lead AI Spam Check On Create" fires
            # inside create(), and message_new() calls it again right after
            # super() returns. Neither knew about the other, so each lead was
            # sent to the model twice. Measured 2026-07-30 across 776 CLI
            # transcripts: 380 distinct prompts but 776 calls, 375 of them
            # sent exactly twice — 51% of this workload was pure duplication,
            # at a median 10.9 s apart and ~8.6 s of exclusive CLI slot each.
            # Carrying either tag means this lead is already classified.
            if spam_tag in lead.tag_ids or pass_tag in lead.tag_ids:
                continue

            body_texts = lead._get_email_chatter_body_texts(limit=8)
            has_email_body = bool(body_texts or (lead.incoming_email_body or "").strip())
            if not has_email_body:
                continue

            is_spam = lead._is_ai_spam_lead()
            if is_spam is None:
                continue

            tag_commands = []
            if is_spam:
                if spam_tag not in lead.tag_ids:
                    tag_commands.append((4, spam_tag.id))
                if pass_tag in lead.tag_ids:
                    tag_commands.append((3, pass_tag.id))
            else:
                if pass_tag not in lead.tag_ids:
                    tag_commands.append((4, pass_tag.id))
                if spam_tag in lead.tag_ids:
                    tag_commands.append((3, spam_tag.id))

            write_vals = {}
            if tag_commands:
                write_vals["tag_ids"] = tag_commands

            if is_spam and "stage_id" in lead._fields:
                spam_stage = lead._find_spam_stage()
                if spam_stage and lead.stage_id != spam_stage:
                    write_vals["stage_id"] = spam_stage.id

            if write_vals:
                lead.sudo().with_context(
                    tracking_disable=True,
                    skip_ai_spam_check=True,
                ).write(write_vals)

    def _run_ai_spam_check_on_create(self):
        self._run_ai_spam_check()
