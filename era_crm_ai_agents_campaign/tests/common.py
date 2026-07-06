# -*- coding: utf-8 -*-
"""Hermetic harness for the Campaign Agent tests.

Every test is offline: the module's single LLM seam
(``crm.ai.campaign.agent._call_llm``) is patched at the model-class level, so
prompt building, reply parsing, grounding validation and the whole engine flow
are genuinely exercised — no native AI, no network, no Claude CLI, no guard
egress. The fake captures each (system, prompt) pair so tests can assert on
data minimization and playbook injection.
"""
import json
from unittest import mock

from odoo.tests import TransactionCase


class CampaignCase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Param = self.env["ir.config_parameter"].sudo()
        self.Campaign = self.env["crm.ai.campaign"]
        self.Line = self.env["crm.ai.campaign.line"]
        self.Catalog = self.env["crm.ai.campaign.service.catalog"]
        self.Playbook = self.env["crm.ai.campaign.playbook"]
        self.Suppression = self.env["crm.ai.campaign.suppression"]
        self.Mailing = self.env["mailing.mailing"]
        self.agent_model = self.env["crm.ai.campaign.agent"]

        # Deterministic candidate pool: hide every pre-existing partner email
        # (test transaction only — rolled back).
        self.env["res.partner"].search(
            [("email", "!=", False)]).write({"email": False})

        # Gates wide open unless a test narrows them; approval OFF by default
        # so flow tests reach the hand-off; PDPL guard OFF by default (the
        # Compliance layer is absent on a clean DB) — PDPL tests turn it on.
        self._set("enabled", "True")
        self._set("llm_transport", "token")
        self._set("require_human_approval", "False")
        self._set("approver_user_ids", " ")
        self._set("pdpl_guard_enabled", "False")
        self._set("daily_limit", "10")
        self._set("max_daily_campaigns", "2")
        self._set("cooldown_days", "30")
        self._set("monthly_frequency_cap", "2")
        self._set("send_window_start", "0.0")
        self._set("send_window_end", "24.0")
        self._set("send_tz", "UTC")
        self._set("blackout_days", " ")
        self._set("confidence_threshold", "0.7")
        self._set("llm_daily_call_cap", "200")
        self._set("default_lang", "ar")
        self._set("email_from", "Era Marketing <marketing@era.example>")

        # One grounded service the fake LLM picks by default.
        self.service = self.Catalog.create({
            "name": "ERP Implementation",
            "service_type": "service",
            "description": "Full Odoo ERP implementation for Saudi SMEs.",
        })

    # ------------------------------------------------------------------
    def _set(self, key, val):
        self.Param.set_param("era_crm_ai_agents_campaign." + key, val)

    def _partner(self, name, email, tags=None, lang=None, phone=None):
        return self.env["res.partner"].create({
            "name": name,
            "email": email,
            "phone": phone,
            "lang": lang,
            "category_id": [(6, 0, tags or [])],
        })

    def _approver(self, login="campaign_approver"):
        """An AI-user approver. No email on the login → the user's partner has
        no email and never enters the candidate pool."""
        user = self.env["res.users"].create({
            "name": "Campaign Approver",
            "login": login,
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("era_crm_ai_agents_base.group_crm_ai_user").id,
            ])],
        })
        self._set("approver_user_ids", str(user.id))
        return user

    def _sent_line(self, partner, sent_date):
        """A historical 'sent' line (drives cooldown / monthly cap). All
        history lines share ONE campaign so the history itself does not eat
        into today's max_daily_campaigns budget."""
        if not getattr(self, "_history_campaign", None):
            self._history_campaign = self.Campaign.create({
                "name": "History", "state": "sent"})
        return self.Line.create({
            "campaign_id": self._history_campaign.id,
            "partner_id": partner.id,
            "state": "sent",
            "sent_date": sent_date,
        })

    def _run(self, reply=None, confidence=0.9, **kw):
        """Patch the LLM seam and run one pass.

        :param reply: full JSON string the fake returns (overrides the
            default well-formed reply), or a callable(prompt) -> str.
        :returns: (result_dict, calls) where calls is a list of
            {'system': ..., 'prompt': ...} captured per LLM call.
        """
        calls = []
        service_id = self.service.id

        def fake_call_llm(self_, prompt, sensitivity="low", system=None,
                          record=None, unattended=False,
                          max_output_tokens=1024):
            calls.append({"system": system or "", "prompt": prompt,
                          "record": record})
            if callable(reply):
                return [reply(prompt)]
            if reply is not None:
                return [reply]
            return [json.dumps({
                "service_id": service_id,
                "subject": "An offer for {{customer_name}}",
                "body": "<p>Dear {{customer_name}}, we can help.</p>",
                "match_confidence": confidence,
            })]

        AgentCls = type(self.agent_model)
        with mock.patch.object(AgentCls, "_call_llm", new=fake_call_llm):
            result = self.agent_model.run_daily_campaign(**kw)
        return result, calls

    def _today_lines(self, state=None):
        domain = []
        if state:
            domain.append(("state", "=", state))
        return self.Line.search(domain)
