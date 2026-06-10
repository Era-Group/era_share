import base64
import json
import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.era_ai_accounts.utils import crypto, llm_cli_transport
from odoo.addons.era_ai_accounts.models import era_ai_account
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
                {"account_id": 1, "home_dir": "/opt/odoo",
                 "min_gap": 0, "gap_per_kb": 0, "lock_wait": 5},
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
                    {"account_id": 1, "home_dir": "/opt/odoo",
                 "min_gap": 0, "gap_per_kb": 0, "lock_wait": 5},
                    "haiku", "sys", "hi", timeout=10)
        msg = str(cm.exception)
        self.assertIn("Out of memory", msg)
        self.assertNotIn('{"type"', msg)  # clean message, not the raw JSON blob

    def test_gap_scales_with_request_size(self):
        cfg = {"min_gap": 1.0, "gap_per_kb": 0.05, "max_gap": 30.0}
        small = llm_cli_transport._compute_gap(cfg, 1024)        # ~1 KB
        big = llm_cli_transport._compute_gap(cfg, 200 * 1024)    # 200 KB
        self.assertGreater(big, small)                           # bigger -> longer wait
        self.assertGreaterEqual(small, 1.0)                      # at least the base gap
        self.assertLessEqual(big, 30.0)                          # capped at max_gap
        # Throttle fully disabled -> no gap.
        self.assertEqual(
            llm_cli_transport._compute_gap({"min_gap": 0, "gap_per_kb": 0, "max_gap": 30}, 9_999_999),
            0.0)

    def test_global_slot_one_at_a_time(self):
        if llm_cli_transport.fcntl is None:
            self.skipTest("fcntl unavailable")
        with llm_cli_transport._global_slot(1, 5):
            # A second acquisition (across the host) must not succeed immediately.
            with self.assertRaises(Exception):
                with llm_cli_transport._global_slot(1, 0):
                    pass

    def test_global_slot_allows_configured_concurrency(self):
        if llm_cli_transport.fcntl is None:
            self.skipTest("fcntl unavailable")
        # With 2 slots, two calls run together; a third is refused immediately.
        with llm_cli_transport._global_slot(2, 5):
            with llm_cli_transport._global_slot(2, 5):
                with self.assertRaises(Exception):
                    with llm_cli_transport._global_slot(2, 0):
                        pass

    def test_cli_gap_enabled_persists_when_unchecked(self):
        Settings = self.env["res.config.settings"]
        icp = self.env["ir.config_parameter"].sudo()
        # Default (no param set) -> ON.
        icp.search([("key", "=", "ai.cli_gap_enabled")]).unlink()
        self.assertTrue(Settings.create({}).cli_gap_enabled)
        # Uncheck + save -> persisted as the string "False" (not deleted).
        rec = Settings.create({})
        rec.cli_gap_enabled = False
        rec._inverse_cli_gap_enabled()
        self.assertEqual(icp.get_param("ai.cli_gap_enabled"), "False")
        # The bug was: a fresh form reverted to True. It must now stay unchecked.
        self.assertFalse(Settings.create({}).cli_gap_enabled)
        # Re-enable round-trips too.
        rec.cli_gap_enabled = True
        rec._inverse_cli_gap_enabled()
        self.assertEqual(icp.get_param("ai.cli_gap_enabled"), "True")
        self.assertTrue(Settings.create({}).cli_gap_enabled)

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

    # ------------------------------------------------------------- Cloudflare
    def test_cloudflare_account_basics(self):
        acc = self.Account.create({
            "name": "CF", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "acct123", "secret": "cf-token",
        })
        self.assertEqual(acc._service_provider(), "cloudflare")
        self.assertEqual(
            acc._cloudflare_base_url(),
            "https://api.cloudflare.com/client/v4/accounts/acct123/ai/v1")
        self.assertEqual(
            acc._cloudflare_run_url("@cf/x"),
            "https://api.cloudflare.com/client/v4/accounts/acct123/ai/run/@cf/x")

    def test_cloudflare_onchange_forces_api_key(self):
        # The form default auth_mode is cli_proxy (Anthropic-only); picking another
        # provider must auto-switch to api_key so the constraint isn't violated.
        acc = self.Account.new({"provider": "anthropic", "auth_mode": "cli_proxy"})
        acc.provider = "cloudflare"
        acc._onchange_provider()
        self.assertEqual(acc.auth_mode, "api_key")

    def test_cloudflare_sync_models_with_rates(self):
        acc = self.Account.create({
            "name": "CF2", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "a", "secret": "t",
        })
        acc.action_sync_models()
        kinds = set(acc.model_ids.mapped("kind"))
        self.assertIn("chat", kinds)
        self.assertIn("image", kinds)
        # Indicative Neuron rate captured at sync time.
        flux = acc.model_ids.filtered(lambda m: m.kind == "image")[:1]
        self.assertTrue(flux and flux.cost_info)
        self.assertEqual(acc._default_chat_model(), "@cf/meta/llama-3.1-8b-instruct")
        self.assertEqual(acc._default_image_model(), "@cf/black-forest-labs/flux-1-schnell")

    def test_cloudflare_generate_image(self):
        acc = self.Account.create({
            "name": "CF3", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "acct", "secret": "tok",
        })
        png = b"\x89PNG-fake-bytes"
        captured = {}

        class _Resp:
            status_code = 200
            headers = {"Content-Type": "application/json"}
            text = ""

            def json(self):
                return {"success": True, "result": {"image": base64.b64encode(png).decode()}}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["headers"] = kwargs.get("headers")
            return _Resp()

        with patch.object(era_ai_account.requests, "post", side_effect=fake_post):
            out = acc.generate_image("a cat on a roof", steps=4)
        self.assertEqual(out, png)
        self.assertIn("/ai/run/@cf/black-forest-labs/flux-1-schnell", captured["url"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(captured["json"]["steps"], 4)

    def test_cloudflare_image_model_choice(self):
        acc = self.Account.create({
            "name": "CFimg", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "acct", "secret": "tok",
        })
        acc.action_sync_models()
        # FLUX.2 models are in the catalog so the admin can pick a better one.
        image_ids = set(acc.model_ids.filtered(lambda m: m.kind == "image").mapped("model_id"))
        self.assertIn("@cf/black-forest-labs/flux-2-dev", image_ids)
        self.assertIn("@cf/black-forest-labs/flux-2-klein-9b", image_ids)
        # An explicit model is passed straight through to the run URL.
        captured = {}

        class _Resp:
            status_code = 200
            headers = {"Content-Type": "application/json"}
            text = ""

            def json(self):
                return {"success": True, "result": {"image": base64.b64encode(b"x").decode()}}

        with patch.object(era_ai_account.requests, "post",
                          side_effect=lambda url, **kw: captured.update(url=url) or _Resp()):
            acc.generate_image("a hero image", model="@cf/black-forest-labs/flux-2-klein-9b")
        self.assertIn("/ai/run/@cf/black-forest-labs/flux-2-klein-9b", captured["url"])

    def test_openai_generate_image(self):
        acc = self.Account.create({
            "name": "GPT", "provider": "openai", "auth_mode": "api_key", "secret": "sk-x",
        })
        png = b"\x89PNG-openai"
        captured = {}

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"data": [{"b64_json": base64.b64encode(png).decode()}]}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["headers"] = kwargs.get("headers")
            return _Resp()

        with patch.object(era_ai_account.requests, "post", side_effect=fake_post):
            out = acc.generate_image("a hero image", model="gpt-image-1")
        self.assertEqual(out, png)
        self.assertIn("/images/generations", captured["url"])
        self.assertEqual(captured["json"]["model"], "gpt-image-1")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-x")

    def test_openai_sync_includes_image_models(self):
        acc = self.Account.create({
            "name": "GPT2", "provider": "openai", "auth_mode": "api_key", "secret": "sk-y",
        })
        # Avoid a live /models call — stub the chat list; image models are appended.
        with patch.object(type(acc), "_http_list_models", return_value=[("gpt-4o", "gpt-4o", "chat")]):
            acc.action_sync_models()
        image_ids = set(acc.model_ids.filtered(lambda m: m.kind == "image").mapped("model_id"))
        self.assertIn("gpt-image-1", image_ids)
        self.assertIn("dall-e-3", image_ids)

    def test_generate_image_unsupported_provider(self):
        acc = self.Account.create({
            "name": "g", "provider": "google", "auth_mode": "api_key", "secret": "x",
        })
        with self.assertRaises(Exception):
            acc.generate_image("anything")

    def test_cloudflare_content_via_agent(self):
        acc = self.Account.create({
            "name": "CFchat", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "acct", "secret": "tok",
        })
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "CF agent", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        captured = {}

        def fake_request(self, method, endpoint, headers=None, body=None, **kwargs):
            captured["endpoint"] = endpoint
            captured["body"] = body
            captured["headers"] = headers
            return {"choices": [{"message": {"content": "cf says hi"}}]}

        with patch.object(LLMApiService, "_request", fake_request):
            out = agent._generate_response("hello")
        self.assertEqual(out, ["cf says hi"])
        self.assertEqual(captured["endpoint"], "/chat/completions")
        self.assertEqual(captured["body"]["model"], "@cf/meta/llama-3.1-8b-instruct")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tok")

    # ------------------------------------------------- Login with Claude (OAuth)
    def _linked_cli_account(self, name="ClaudeLink"):
        """A cli_proxy account whose managed credential dir is a throwaway tmpdir."""
        acc = self.Account.create({
            "name": name, "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        tmp = tempfile.mkdtemp(prefix="era_ai_test_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Redirect the managed HOME to the tmpdir so tests never touch data_dir.
        patcher = patch.object(type(acc), "_cli_managed_home", return_value=tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        return acc, tmp

    def _oauth_token_response(self, **over):
        data = {
            "access_token": "tok-access", "refresh_token": "tok-refresh",
            "expires_in": 3600, "scope": era_ai_account.OAUTH_SCOPES,
            "subscription_type": "max",
        }
        data.update(over)

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return data
        return _Resp()

    def test_oauth_start_returns_url_and_stashes_pkce(self):
        acc, _tmp = self._linked_cli_account()
        url = acc._oauth_start()
        self.assertIn(era_ai_account.OAUTH_AUTHORIZE_URL, url)
        self.assertIn("code_challenge=", url)
        self.assertIn(era_ai_account.OAUTH_CLIENT_ID, url)
        raw = self.env["ir.config_parameter"].sudo().get_param(
            era_ai_account._PKCE_PARAM % acc.id)
        self.assertTrue(json.loads(raw).get("verifier"))

    def test_oauth_complete_links_account(self):
        acc, tmp = self._linked_cli_account()
        acc._oauth_start()
        state = json.loads(self.env["ir.config_parameter"].sudo().get_param(
            era_ai_account._PKCE_PARAM % acc.id))["state"]
        with patch.object(era_ai_account.requests, "post",
                          return_value=self._oauth_token_response()):
            acc._oauth_complete("the-auth-code#%s" % state)
        # Credentials written to the managed dir in Claude Code's format.
        creds_path = os.path.join(tmp, ".claude", ".credentials.json")
        self.assertTrue(os.path.exists(creds_path))
        with open(creds_path) as f:
            oauth = json.load(f)["claudeAiOauth"]
        self.assertEqual(oauth["accessToken"], "tok-access")
        self.assertEqual(oauth["refreshToken"], "tok-refresh")
        self.assertEqual(oauth["subscriptionType"], "max")
        # The record now reports linked + a friendly label, and the PKCE state is consumed.
        acc.invalidate_recordset(["cli_oauth_linked", "cli_oauth_label"])
        self.assertTrue(acc.cli_oauth_linked)
        self.assertIn("max", acc.cli_oauth_label)
        self.assertFalse(self.env["ir.config_parameter"].sudo().get_param(
            era_ai_account._PKCE_PARAM % acc.id))

    def test_oauth_complete_rejects_state_mismatch(self):
        acc, _tmp = self._linked_cli_account()
        acc._oauth_start()
        with self.assertRaises(UserError):
            acc._oauth_complete("code#totally-wrong-state")

    def test_oauth_complete_needs_login_in_progress(self):
        acc, _tmp = self._linked_cli_account()
        with self.assertRaises(UserError):
            acc._oauth_complete("code#state")  # never called _oauth_start

    def test_cli_cfg_uses_managed_dir_when_linked(self):
        acc, tmp = self._linked_cli_account()
        # Not linked yet -> ambient HOME, no config_dir.
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["home_dir"], "/opt/odoo")
        self.assertFalse(cfg["config_dir"])
        # Link it, then the cfg points the CLI at the isolated dir.
        acc._oauth_start()
        state = json.loads(self.env["ir.config_parameter"].sudo().get_param(
            era_ai_account._PKCE_PARAM % acc.id))["state"]
        with patch.object(era_ai_account.requests, "post",
                          return_value=self._oauth_token_response()):
            acc._oauth_complete("code#%s" % state)
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["home_dir"], tmp)
        self.assertEqual(cfg["config_dir"], os.path.join(tmp, ".claude"))

    def test_oauth_logout_removes_credentials(self):
        acc, tmp = self._linked_cli_account()
        acc._oauth_start()
        state = json.loads(self.env["ir.config_parameter"].sudo().get_param(
            era_ai_account._PKCE_PARAM % acc.id))["state"]
        with patch.object(era_ai_account.requests, "post",
                          return_value=self._oauth_token_response()):
            acc._oauth_complete("code#%s" % state)
        self.assertTrue(acc._cli_is_linked())
        acc.action_ai_claude_logout()
        self.assertFalse(acc._cli_is_linked())
        self.assertFalse(os.path.exists(os.path.join(tmp, ".claude", ".credentials.json")))

    def test_oauth_requires_manager(self):
        acc, _tmp = self._linked_cli_account()
        user = new_test_user(self.env, login="era_nomgr", groups="base.group_user")
        with self.assertRaises(UserError):
            acc.with_user(user)._oauth_start()

    def test_oauth_start_rejects_api_key_account(self):
        acc = self.Account.create({
            "name": "keyacc", "provider": "openai", "auth_mode": "api_key", "secret": "sk",
        })
        with self.assertRaises(UserError):
            acc._oauth_start()

    def test_login_wizard_opens_and_completes(self):
        acc, tmp = self._linked_cli_account()
        wiz = self.env["era.ai.account.login"].with_context(
            default_account_id=acc.id).create({})
        # Opening the wizard mints the authorize URL.
        self.assertIn(era_ai_account.OAUTH_AUTHORIZE_URL, wiz.authorize_url or "")
        state = json.loads(self.env["ir.config_parameter"].sudo().get_param(
            era_ai_account._PKCE_PARAM % acc.id))["state"]
        wiz.code = "code#%s" % state
        with patch.object(era_ai_account.requests, "post",
                          return_value=self._oauth_token_response()):
            wiz.action_complete()
        self.assertTrue(acc._cli_is_linked())

    def test_account_generate_text(self):
        # Provider-agnostic content helper any module can call (no ai.agent needed).
        acc = self.Account.create({
            "name": "CFtext", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "acct", "secret": "tok",
        })
        acc.action_sync_models()

        def fake_request(self, method, endpoint, headers=None, body=None, **kwargs):
            return {"choices": [{"message": {"content": "hello from cf"}}]}

        with patch.object(LLMApiService, "_request", fake_request):
            text = acc.generate_text("hi", system="be brief")
        self.assertEqual(text, "hello from cf")
