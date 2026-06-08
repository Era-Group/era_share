from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.era_ai_accounts.utils import crypto, llm_cli_transport
from odoo.addons.ai.utils.llm_api_service import LLMApiService


@tagged("post_install", "-at_install", "era_ai_accounts")
class TestEraAiAccounts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env["era.ai.account"]

    # --------------------------------------------------------------- secrets
    def test_secret_encryption_roundtrip(self):
        acc = self.Account.create({
            "name": "OpenAI key", "provider": "openai",
            "auth_mode": "api_key", "secret": "sk-test-123",
        })
        self.assertTrue(acc.secret_is_set)
        self.assertNotIn("sk-test-123", acc.secret_encrypted or "",
                         "raw key must not be stored in clear")
        self.assertEqual(acc._get_secret(), "sk-test-123")
        # The write-only field never echoes the plaintext back on a fresh read.
        acc.invalidate_recordset(["secret"])
        self.assertFalse(acc.secret)

    def test_crypto_helpers(self):
        cipher = crypto.encrypt_secret(self.env, "hello")
        self.assertNotEqual(cipher, "hello")
        self.assertEqual(crypto.decrypt_secret(self.env, cipher), "hello")
        self.assertEqual(crypto.decrypt_secret(self.env, ""), "")

    # ----------------------------------------------------------- model catalog
    def test_sync_cli_models_and_default(self):
        acc = self.Account.create({
            "name": "Claude", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        acc.action_sync_models()
        self.assertEqual(set(acc.model_ids.mapped("model_id")),
                         {"opus", "sonnet", "haiku"})
        # Preferred alias wins even though alphabetical order differs.
        self.assertEqual(acc._default_chat_model(), "opus")

    def test_default_model_without_catalog(self):
        acc = self.Account.create({
            "name": "Claude2", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        self.assertEqual(acc._default_chat_model(), "opus")

    def test_service_provider_token(self):
        cli = self.Account.create({"name": "c", "provider": "anthropic", "auth_mode": "cli_proxy"})
        self.assertEqual(cli._service_provider(), "anthropic_cli")
        key = self.Account.create({"name": "k", "provider": "anthropic", "auth_mode": "api_key"})
        self.assertEqual(key._service_provider(), "anthropic")
        custom = self.Account.create({"name": "x", "provider": "custom", "auth_mode": "api_key"})
        self.assertEqual(custom._service_provider(), "custom_llm")

    # --------------------------------------------------------------- sharing
    def test_personal_account_isolation(self):
        user_a = new_test_user(self.env, login="era_a", groups="base.group_user")
        user_b = new_test_user(self.env, login="era_b", groups="base.group_user")
        acc = self.Account.create({
            "name": "A personal", "provider": "anthropic", "auth_mode": "cli_proxy",
            "scope": "personal", "owner_user_id": user_a.id,
        })
        # Owner sees it, the other user does not (ir.rule).
        self.assertIn(acc, self.Account.with_user(user_a).search([]))
        self.assertNotIn(acc, self.Account.with_user(user_b).search([]))
        # Resolution mirrors the rule.
        self.assertEqual(self.Account._resolve_for_user(user_a, provider="anthropic"), acc)
        self.assertFalse(self.Account._resolve_for_user(user_b, provider="anthropic"))

    def test_shared_account_visible_to_all(self):
        user_b = new_test_user(self.env, login="era_c", groups="base.group_user")
        acc = self.Account.create({
            "name": "Shared", "provider": "anthropic", "auth_mode": "cli_proxy",
            "scope": "shared",
        })
        self.assertIn(acc, self.Account.with_user(user_b).search([]))

    def test_secret_hidden_from_regular_user(self):
        user_b = new_test_user(self.env, login="era_d", groups="base.group_user")
        acc = self.Account.create({
            "name": "Shared2", "provider": "openai", "auth_mode": "api_key",
            "scope": "shared", "secret": "sk-secret",
        })
        with self.assertRaises(AccessError):
            acc.with_user(user_b).read(["secret_encrypted"])

    # ------------------------------------------------------------- CLI routing
    def test_cli_complete_builds_args_and_parses(self):
        captured = {}

        class _Proc:
            returncode = 0
            stdout = '{"type":"result","result":"hi there","is_error":false}'
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            captured["env"] = kwargs.get("env")
            return _Proc()

        with patch.object(llm_cli_transport, "resolve_cli_binary", return_value="/usr/bin/claude"), \
             patch.object(llm_cli_transport.subprocess, "run", side_effect=fake_run):
            text = llm_cli_transport.cli_complete(
                {"account_id": 1, "home_dir": "/opt/odoo", "max_concurrency": 1},
                "claude-opus-4-8", "system here", "user asks", timeout=30)
        self.assertEqual(text, "hi there")
        self.assertIn("-p", captured["args"])
        self.assertIn("--model", captured["args"])
        self.assertIn("claude-opus-4-8", captured["args"])
        self.assertEqual(captured["input"], "user asks")
        self.assertEqual(captured["env"]["HOME"], "/opt/odoo")

    def test_extract_cli_error(self):
        self.assertEqual(
            llm_cli_transport._extract_cli_error('{"is_error":true,"result":"boom"}'), "boom")
        self.assertEqual(llm_cli_transport._extract_cli_error(""), "")

    def test_cli_error_surfaces_clean_message(self):
        class _Proc:
            returncode = 1
            stdout = '{"type":"result","is_error":true,"result":"API Error: Out of memory"}'
            stderr = ""

        with patch.object(llm_cli_transport, "resolve_cli_binary", return_value="/usr/bin/claude"), \
             patch.object(llm_cli_transport.subprocess, "run", return_value=_Proc()):
            with self.assertRaises(Exception) as cm:
                llm_cli_transport.cli_complete(
                    {"account_id": 1, "home_dir": "/opt/odoo", "max_concurrency": 1},
                    "haiku", "sys", "hi", timeout=10)
        msg = str(cm.exception)
        self.assertIn("Out of memory", msg)
        self.assertNotIn('{"type"', msg)  # clean message, not the raw JSON blob

    def test_agent_routes_through_cli_account(self):
        acc = self.Account.create({
            "name": "Claude route", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "Routed", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        with patch.object(llm_cli_transport, "cli_complete", return_value="proxied answer") as mocked:
            out = agent._generate_response("hello")
        self.assertEqual(out, ["proxied answer"])
        self.assertTrue(mocked.called)
        # The selected model id was passed to the transport.
        self.assertEqual(mocked.call_args.args[1], "opus")
