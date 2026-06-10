# -*- coding: utf-8 -*-
"""Claude CLI / subscription transport: full enforcement + token-limit tests.

Two things are proven here:

1. SAFETY (the PDPL line): when our agents route a CLI call, the guard masks real
   PII BEFORE the prompt reaches the transport. The real Arabic name + phone never
   leave; only placeholders do. We drive the pipeline with a fake transport and
   assert exactly what it received — the real Claude CLI is never invoked.

2. CONSUMPTION control: the CLI path is governed by an estimated-TOKEN limit (not
   the dollar cap), is exempt from the unpriced-model rate-card block, and its
   usage rows are flagged ``via_cli`` + ``token_source=estimated``.

The guard derives ``is_cli`` from ``service.provider == 'anthropic_cli'``. We use a
minimal stub service (only ``.env`` + ``.provider`` are read) so these tests stay
decoupled from era_ai_accounts' patched __init__ and never shell out to a CLI.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.era_crm_ai_agents_base.services import ai_compliance_guard as guard

_GUARD_LOGGER = "odoo.addons.era_crm_ai_agents_base.services.ai_compliance_guard"


class _StubCliService:
    """Stand-in for an anthropic_cli LLMApiService — the guard only reads .env
    and .provider. Avoids constructing the real (era_ai_accounts-patched) class."""

    provider = guard.CLI_PROVIDER

    def __init__(self, env):
        self.env = env


@tagged("post_install", "-at_install")
class TestCliTokenPath(TransactionCase):

    def setUp(self):
        super().setUp()
        # Generous global token limit so consumption tests that should PASS are
        # not tripped by the seeded default; per-agent overrides set per test.
        self.env["ir.config_parameter"].sudo().set_param(
            "era_crm_ai_agents.monthly_token_limit", "100000000")

    def _cli_ctx(self, **ctx):
        return self.env(context=dict(self.env.context, **ctx))

    # ------------------------------------------------------------------ SAFETY
    def test_redaction_before_transport_arabic(self):
        """THE safety test: real Arabic name + phone NEVER reach the CLI transport;
        only placeholders do, and the response is restored on the way back."""
        partner = self.env["res.partner"].create({
            "name": "محمد عبدالله القحطاني",
            "phone": "+966501234567",
            "crm_ai_intl_processing_consent": True,
        })
        self.env["crm.ai.agent"].sudo().create(
            {"name": "CLI Safety", "tech_name": "era_cli_safety"})

        captured = {}

        def fake_transport(masked_system, masked_user):
            captured["system"] = list(masked_system)
            captured["user"] = list(masked_user)
            # Model echoes the placeholders back (good case).
            return ["تم التواصل مع [[PII:PERSON:1]] على [[PII:PHONE:1]]"]

        svc = _StubCliService(self._cli_ctx(
            **{guard.CTX_AGENT: "era_cli_safety", guard.CTX_RECORD: partner}))
        out = guard._guard_text(
            svc, fake_transport, "opus",
            ["أنت مساعد مبيعات"],
            ["راسل العميل محمد عبدالله القحطاني على 0501234567"])

        blob = " ".join(captured["system"] + captured["user"])
        # Real PII must NOT have reached the transport — in any form.
        self.assertNotIn("محمد عبدالله القحطاني", blob)
        self.assertNotIn("القحطاني", blob)          # family name alone either
        self.assertNotIn("0501234567", blob)          # local phone form
        self.assertNotIn("+966501234567", blob)       # international form
        # Placeholders DID reach it.
        self.assertIn("[[PII:PERSON", blob)
        self.assertIn("[[PII:PHONE", blob)
        # Restored on the way back (strict re-insertion).
        self.assertIn("محمد عبدالله القحطاني", out[0])
        self.assertIn("+966501234567", out[0])

    def test_cli_call_routes_to_strict_full_enforcement(self):
        """A mixin-style CLI call (CTX_AGENT set) hits _text_strict: an unmapped
        phone HARD-BLOCKS — proving it is NOT the best-effort native path."""
        partner = self.env["res.partner"].create({
            "name": "Strict CLI", "phone": "+966500000009",
            "crm_ai_intl_processing_consent": True,
        })
        self.env["crm.ai.agent"].sudo().create(
            {"name": "CLI Strict", "tech_name": "era_cli_strict"})
        sent = {"hit": False}

        def fake(masked_system, masked_user):
            sent["hit"] = True
            return ["should not be reached"]

        svc = _StubCliService(self._cli_ctx(
            **{guard.CTX_AGENT: "era_cli_strict", guard.CTX_RECORD: partner}))
        with self.assertRaises(UserError):
            guard._guard_text(svc, fake, "opus", ["s"],
                              ["also call the other number 0559876543"])
        self.assertFalse(sent["hit"], "unmapped PII must block before any CLI call")

    # --------------------------------------------------------- consumption ctrl
    def test_cli_exempt_from_unpriced_block(self):
        """The unpriced-model block is dollar-path only. A CLI alias ('opus') is
        never in the rate card, yet the call proceeds and records via_cli usage."""
        self.env["ir.config_parameter"].sudo().set_param(
            "era_crm_ai_agents.block_unpriced_model", "True")
        self.env["crm.ai.agent"].sudo().create(
            {"name": "CLI Unpriced", "tech_name": "era_cli_unpriced"})
        sent = {"hit": False}

        def fake(masked_system, masked_user):
            sent["hit"] = True
            return ["ok"]

        svc = _StubCliService(self._cli_ctx(**{guard.CTX_AGENT: "era_cli_unpriced"}))
        out = guard._guard_text(svc, fake, "opus", ["s"], ["hello there"])
        self.assertTrue(sent["hit"], "CLI must be exempt from the unpriced block")
        self.assertEqual(out, ["ok"])

        usage = self.env["crm.ai.usage"].sudo().search(
            [("agent_id.tech_name", "=", "era_cli_unpriced")], order="id desc", limit=1)
        self.assertTrue(usage.via_cli)
        self.assertEqual(usage.token_source, "estimated")
        self.assertEqual(usage.cost, 0.0)
        self.assertGreater(usage.total_tokens, 0, "tokens must be recorded for the limit")

    def test_cli_token_limit_blocks_and_caps(self):
        """Over the monthly token limit -> block + auto-pause (capped) + audit,
        nothing sent.

        We catch the block with a plain ``try/except`` rather than ``assertRaises``
        on purpose: Odoo's ``assertRaises`` wraps the body in a savepoint and rolls
        it back when it catches the error, which would undo the very side effects
        (capped state + audit row) this test must verify. The production safety
        property (nothing sent) holds either way; here we assert the durable
        auto-pause + audit that the block also performs.
        """
        agent = self.env["crm.ai.agent"].sudo().create(
            {"name": "CLI Limit", "tech_name": "era_cli_limit"})
        self.env["ir.config_parameter"].sudo().set_param(
            "era_crm_ai_agents.monthly_token_limit.era_cli_limit", "10")
        # Pre-load this month's usage over the limit (CLI rows).
        self.env["crm.ai.usage"].record(agent, False, 50, 50, 0.0, via_cli=True)
        sent = {"hit": False}

        def fake(masked_system, masked_user):
            sent["hit"] = True
            return ["x"]

        svc = _StubCliService(self._cli_ctx(**{guard.CTX_AGENT: "era_cli_limit"}))
        raised = False
        try:
            guard._guard_text(svc, fake, "opus", ["s"], ["hi"])
        except UserError:
            raised = True
        self.assertTrue(raised, "over-limit must raise (block)")
        self.assertFalse(sent["hit"], "over-limit must block before any CLI call")
        self.assertEqual(agent.state, "capped", "agent must auto-pause to capped")
        self.assertTrue(
            self.env["crm.ai.audit.log"].sudo().search_count(
                [("agent_id", "=", agent.id), ("event_type", "=", "blocked")]),
            "the block must be audited (Rule 20)")

    def test_tripwire_warns_on_cli_without_ctx_agent(self):
        """A CLI call lacking CTX_AGENT drops to best-effort but logs a tripwire."""
        sent = {"hit": False}

        def fake(masked_system, masked_user):
            sent["hit"] = True
            return ["native ok"]

        svc = _StubCliService(self.env)  # no CTX_AGENT
        with self.assertLogs(_GUARD_LOGGER, level="WARNING") as cm:
            out = guard._guard_text(svc, fake, "opus", ["s"], ["hello"])
        self.assertTrue(sent["hit"], "best-effort native path still runs")
        self.assertEqual(out, ["native ok"])
        self.assertTrue(any("TRIPWIRE" in line for line in cm.output),
                        "a CLI call without CTX_AGENT must trip the warning")


@tagged("post_install", "-at_install")
class TestAgentCliWiring(TransactionCase):
    """Agent-side wiring for the CLI transport (constraint + path-aware limit)."""

    def test_cli_transport_requires_account(self):
        with self.assertRaises(ValidationError):
            self.env["crm.ai.agent"].sudo().create(
                {"name": "No Acct", "tech_name": "era_cli_noacct", "transport": "cli"})

    def test_is_over_limit_uses_token_path_for_cli_agent(self):
        account = self.env["era.ai.account"].sudo().create({"name": "Test CLI Acct"})
        agent = self.env["crm.ai.agent"].sudo().create({
            "name": "With Acct", "tech_name": "era_cli_acct",
            "transport": "cli", "cli_account_id": account.id,
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "era_crm_ai_agents.monthly_token_limit.era_cli_acct", "10")
        self.env["crm.ai.usage"].record(agent, False, 30, 30, 0.0, via_cli=True)
        # _is_over_limit must consult the TOKEN limit (not the dollar cap) here.
        self.assertTrue(self.env["crm.ai.usage"]._is_over_limit(agent))
