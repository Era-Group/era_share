# -*- coding: utf-8 -*-
"""The Campaign agent — the module's single LLM seam.

AbstractModel inheriting ``crm.ai.agent.mixin``: every LLM call runs through
the mixin's ``_call_llm`` so the base AI Compliance Guard governs every prompt
(consent, PII redaction, cost/token limit, audit). This module NEVER builds
``LLMApiService``, never imports an HTTP/LLM client, and has NO non-LLM
network egress at all.

Transport (manager-selectable, setting ``campaign_llm_transport``):
  * ``token``           → the agent registry row runs transport='api'
                          (native OpenAI/Google, env-only key — Rule 03).
  * ``era_ai_accounts`` → the registry row runs transport='cli' through the
                          editor-subscription proxy. Runtime-OPTIONAL: when
                          selected but unavailable (module absent, or no
                          usable account bound), the run FAILS CLOSED.
Both paths land in the same base egress seam; the setting only decides which
transport the pre-seeded ``crm.ai.agent`` row must be configured with, and the
engine refuses to run on any mismatch (fail closed, audited).
"""
import json
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class CrmAiCampaignAgent(models.AbstractModel):
    _name = "crm.ai.campaign.agent"
    _description = "CRM AI Campaign Agent"
    _inherit = ["crm.ai.agent.mixin"]

    # Stable registry key — matches the seeded crm.ai.agent row.
    _agent_tech_name = "era_campaign"

    # ------------------------------------------------------------------
    # Entrypoints
    # ------------------------------------------------------------------
    def run_daily_campaign(self, unattended=False):
        """Run one daily campaign pass. Called by the cron (unattended=True)
        and by the manager's manual-run action. Imported lazily so the
        services package loads only when a run is actually triggered."""
        from odoo.addons.era_crm_ai_agents_campaign.services.campaign_engine import (
            CampaignEngine,
        )
        return CampaignEngine(self, unattended=unattended).run()

    def _cron_run_daily_campaign(self):
        """Scheduled daily run. The cron code resolves the suite-wide run user
        itself (see data/ir_cron_data.xml); this wrapper only fixes the
        unattended flag."""
        return self.run_daily_campaign(unattended=True)

    # ------------------------------------------------------------------
    # Transport availability (runtime-optional era_ai_accounts)
    # ------------------------------------------------------------------
    def _era_ai_accounts_available(self):
        """True when the era_ai_accounts transport can actually be used:
        the module's model is in the registry AND the agent registry row is
        bound to the CLI transport with an account. Registry check only —
        never a hard import (era_ai_accounts is runtime-optional here)."""
        if "era.ai.account" not in self.env:
            return False
        agent = self._get_agent_record()
        return agent.transport == "cli" and bool(agent.cli_account_id)

    # ------------------------------------------------------------------
    # LLM matching + drafting (the single egress seam)
    # ------------------------------------------------------------------
    def _llm_match_and_draft(self, payload, services, instructions, lang,
                             record=None, unattended=False):
        """One LLM call: pick a service for one partner and draft the email.

        :param payload: dict of MINIMIZED business attributes (built by the
            engine; contains no PII when the PDPL guard is on).
        :param services: list of dicts {id, name, type, description} — the
            ACTIVE catalog subset offered to the model. The reply is only
            accepted if it picks one of these ids (grounding).
        :param instructions: list of playbook instruction strings to inject.
        :param lang: 'ar' or 'en' — the drafting language.
        :param record: optional record for the guard's redaction/consent
            context (passed when the payload is NOT minimized).
        :returns: dict {service_id, subject, body, match_confidence} or None
            when the reply is unusable (the caller records the skip).
        """
        lang_name = "Arabic" if lang == "ar" else "English"
        system = (
            "You are a B2B email-marketing assistant for Era Group (Odoo "
            "services, Saudi Arabia). You will receive a customer profile and "
            "a numbered list of services. Choose EXACTLY ONE service from the "
            "list — never invent one — and draft a short, professional "
            "marketing email in %s. Address the recipient with the literal "
            "placeholder {{customer_name}} (it is substituted later; you never "
            "see real names). Return ONLY a JSON object, no prose, no markdown "
            "fences, with exactly these keys: "
            '{"service_id": <int, the id of the chosen service>, '
            '"subject": <str>, "body": <str, simple HTML paragraphs>, '
            '"match_confidence": <float 0..1, how well the service fits>}.'
            % lang_name
        )
        prompt_parts = [
            "CUSTOMER PROFILE (business attributes):\n%s"
            % json.dumps(payload, ensure_ascii=False),
            "AVAILABLE SERVICES (choose one by id):\n%s"
            % json.dumps(services, ensure_ascii=False),
        ]
        if instructions:
            prompt_parts.append(
                "SPECIAL ROUTING INSTRUCTIONS (apply all, in order):\n- "
                + "\n- ".join(instructions))
        reply = self._call_llm(
            "\n\n".join(prompt_parts),
            sensitivity="low",
            system=system,
            record=record,
            unattended=unattended,
        )
        return self._parse_reply("".join(reply or []))

    @staticmethod
    def _parse_reply(text):
        """Best-effort: pull the outermost JSON object out of a model reply.
        Returns None on junk — a run must survive a bad reply."""
        text = (text or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            _logger.warning(
                "Campaign agent: could not parse LLM reply as JSON.")
            return None
        if not isinstance(data, dict):
            return None
        try:
            service_id = int(data.get("service_id"))
            confidence = max(0.0, min(1.0, float(
                data.get("match_confidence", 0.0))))
        except (TypeError, ValueError):
            return None
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
        if not subject or not body:
            return None
        return {
            "service_id": service_id,
            "subject": subject,
            "body": body,
            "match_confidence": confidence,
        }
