import base64
import json
import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.era_ai_accounts.utils import codex_cli_transport, crypto, llm_cli_transport
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
        codex = self.Account.create({"name": "cx", "provider": "openai", "auth_mode": "cli_proxy"})
        self.assertEqual(codex._service_provider(), "openai_cli")
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

    def test_openai_transcribe(self):
        acc = self.Account.create({
            "name": "GPTaudio", "provider": "openai", "auth_mode": "api_key", "secret": "sk-a",
        })
        captured = {}

        class _Resp:
            status_code = 200
            headers = {"Content-Type": "application/json"}
            text = '{"text": "hello world"}'

            def json(self):
                return {"text": "hello world"}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["data"] = kwargs.get("data")
            captured["files"] = kwargs.get("files")
            captured["headers"] = kwargs.get("headers")
            return _Resp()

        with patch.object(era_ai_account.requests, "post", side_effect=fake_post):
            text = acc.transcribe(b"RIFF-fake-audio", filename="memo.mp3", language="en")
        self.assertEqual(text, "hello world")
        self.assertIn("/audio/transcriptions", captured["url"])
        self.assertEqual(captured["data"]["model"], "gpt-4o-transcribe")
        self.assertEqual(captured["data"]["language"], "en")
        # Multipart upload: the audio rides in files=, not JSON, and we must NOT
        # force a Content-Type (requests sets it with the boundary).
        self.assertIn("file", captured["files"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-a")
        self.assertNotIn("Content-Type", captured["headers"])

    def test_transcribe_unsupported_provider(self):
        acc = self.Account.create({
            "name": "tg", "provider": "google", "auth_mode": "api_key", "secret": "x",
        })
        with self.assertRaises(UserError):
            acc.transcribe(b"audio")

    def test_codex_cli_proxy_refuses_transcribe(self):
        acc = self.Account.create({
            "name": "Codex audio", "provider": "openai", "auth_mode": "cli_proxy",
        })
        with self.assertRaises(UserError):
            acc.transcribe(b"audio")

    def test_cloudflare_coerces_non_string_content(self):
        # Live crash: Cloudflare returned message.content as a dict and
        # _request_llm_cloudflare did content.strip() -> 'dict' has no 'strip'.
        from odoo.addons.era_ai_accounts.models.llm_service_patch import _coerce_message_text
        self.assertEqual(_coerce_message_text("hi"), "hi")
        self.assertEqual(_coerce_message_text(None), "")
        self.assertEqual(_coerce_message_text({"type": "text", "text": "blk"}), "blk")
        self.assertEqual(
            _coerce_message_text([{"text": "a"}, {"text": "b"}, "c"]), "abc")
        # End-to-end: a dict-content response must not crash the transport.
        acc = self.Account.create({
            "name": "CFdict", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "acct", "secret": "tok",
        })
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "CFdict agent", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })

        def fake_request(self, method, endpoint, headers=None, body=None, **kwargs):
            return {"choices": [{"message": {"content": {"type": "text", "text": "ok"}}}]}

        with patch.object(LLMApiService, "_request", fake_request):
            out = agent._generate_response("hello")
        self.assertEqual(out, ["ok"])

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
    def _linked_cli_account(self, name="ClaudeLink", provider="anthropic"):
        """A cli_proxy account whose managed credential dir is a throwaway tmpdir."""
        acc = self.Account.create({
            "name": name, "provider": provider, "auth_mode": "cli_proxy",
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

    def test_oauth_complete_rejects_missing_state(self):
        # Strict CSRF check: pasting only the code (no #state) must be refused.
        acc, _tmp = self._linked_cli_account()
        acc._oauth_start()
        with self.assertRaises(UserError):
            acc._oauth_complete("just-the-code-no-state")

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

    # ------------------------------------------- OpenAI via Codex CLI (no API key)
    @staticmethod
    def _codex_auth_json(plan="pro", **over):
        """A ChatGPT-mode auth.json payload, with a decodable id_token plan claim."""
        claims = base64.urlsafe_b64encode(
            json.dumps({"https://api.openai.com/auth": {"chatgpt_plan_type": plan}}).encode()
        ).rstrip(b"=").decode()
        data = {
            "OPENAI_API_KEY": None,
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": "eyJoZWFkZXIifQ.%s.sig" % claims,
                "access_token": "at-123",
                "refresh_token": "v1.M-rt-456",
                "account_id": "acct-789",
            },
            "last_refresh": "2026-06-11T00:00:00Z",
        }
        data.update(over)
        return data

    def test_openai_cli_proxy_gate(self):
        # OpenAI may now use the CLI proxy; other non-Anthropic providers still can't.
        acc = self.Account.create({
            "name": "Codex", "provider": "openai", "auth_mode": "cli_proxy",
        })
        self.assertTrue(acc)
        with self.assertRaises(ValidationError):
            self.Account.create({
                "name": "bad", "provider": "google", "auth_mode": "cli_proxy",
            })
        # The onchange keeps cli_proxy when switching anthropic -> openai ...
        draft = self.Account.new({"provider": "anthropic", "auth_mode": "cli_proxy"})
        draft.provider = "openai"
        draft._onchange_provider()
        self.assertEqual(draft.auth_mode, "cli_proxy")
        # ... and still flips it for providers without a CLI.
        draft.provider = "google"
        draft._onchange_provider()
        self.assertEqual(draft.auth_mode, "api_key")

    def test_sync_codex_models_and_default(self):
        acc = self.Account.create({
            "name": "Codex2", "provider": "openai", "auth_mode": "cli_proxy",
        })
        acc.action_sync_models()
        self.assertEqual(set(acc.model_ids.mapped("model_id")),
                         {m[0] for m in era_ai_account.CODEX_CLI_MODELS})
        self.assertEqual(acc._default_chat_model(), era_ai_account.CODEX_CLI_MODELS[0][0])
        # All curated Codex models are chat models (no images through the CLI).
        self.assertEqual(set(acc.model_ids.mapped("kind")), {"chat"})

    def test_codex_complete_builds_args_and_parses(self):
        captured = {}

        class _Proc:
            returncode = 0
            stdout = "\n".join([
                '{"type":"thread.started","thread_id":"t1"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"codex says hi"}}',
                '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
            ])
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            captured["env"] = kwargs.get("env")
            return _Proc()

        with patch.object(codex_cli_transport, "resolve_cli_binary", return_value="/usr/bin/codex"), \
             patch.object(codex_cli_transport.subprocess, "run", side_effect=fake_run), \
             patch.dict(codex_cli_transport.os.environ,
                        {"OPENAI_API_KEY": "sk-leak", "CODEX_API_KEY": "ck-leak"}):
            text = codex_cli_transport.cli_complete(
                {"account_id": 1, "home_dir": "/opt/odoo", "config_dir": "/tmp/x/.codex",
                 "min_gap": 0, "gap_per_kb": 0, "lock_wait": 5},
                "gpt-5.3-codex", "system here", "user asks", timeout=30)
        self.assertEqual(text, "codex says hi")
        args = captured["args"]
        self.assertIn("exec", args)
        self.assertIn("--json", args)
        self.assertIn("--model", args)
        self.assertIn("gpt-5.3-codex", args)
        self.assertEqual(args[-1], "-")  # prompt comes from stdin
        # Locked down to pure text generation — every flag is load-bearing:
        # losing any of them silently re-enables capabilities on a customer
        # ChatGPT account (shell, web search, permissive user config).
        self.assertIn("--sandbox", args)
        self.assertIn("read-only", args)
        self.assertIn("--disable", args)
        self.assertIn("shell_tool", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("--skip-git-repo-check", args)
        self.assertIn('web_search="disabled"', args)
        self.assertIn("--ignore-user-config", args)
        self.assertIn("never", args[args.index("--color") + 1])
        # System prompt folded into the stdin document ahead of the user turn.
        self.assertIn("system here", captured["input"])
        self.assertIn("user asks", captured["input"])
        self.assertLess(captured["input"].index("system here"),
                        captured["input"].index("user asks"))
        # Env: account credentials dir exported, API keys never inherited.
        self.assertEqual(captured["env"]["CODEX_HOME"], "/tmp/x/.codex")
        self.assertEqual(captured["env"]["HOME"], "/opt/odoo")
        self.assertNotIn("OPENAI_API_KEY", captured["env"])
        self.assertNotIn("CODEX_API_KEY", captured["env"])

    def test_codex_jsonl_error_event_surfaces_clean_message(self):
        # Codex can exit 0 on error paths — the events are authoritative.
        stdout = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"error","message":"stream disconnected: 401 Unauthorized"}',
        ])
        with self.assertRaises(UserError) as cm:
            codex_cli_transport._parse_codex_jsonl(stdout, 0, "")
        self.assertIn("401 Unauthorized", str(cm.exception))
        self.assertNotIn('{"type"', str(cm.exception))

    def test_codex_jsonl_turn_failed_event(self):
        stdout = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.failed","error":{"message":"usage limit reached"}}',
        ])
        with self.assertRaises(UserError) as cm:
            codex_cli_transport._parse_codex_jsonl(stdout, 0, "")
        self.assertIn("usage limit reached", str(cm.exception))

    def test_codex_validate_runs_login_status(self):
        acc, tmp = self._linked_cli_account(name="CodexVal", provider="openai")
        acc._codex_link_with_auth_json(json.dumps(self._codex_auth_json()))
        captured = {}

        class _Proc:
            returncode = 0
            stdout = "Logged in using ChatGPT"
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            return _Proc()

        with patch.object(codex_cli_transport, "resolve_cli_binary", return_value="/usr/bin/codex"), \
             patch.object(codex_cli_transport.subprocess, "run", side_effect=fake_run):
            acc.action_validate()
        self.assertEqual(acc.state, "valid")
        self.assertEqual(captured["args"], ["/usr/bin/codex", "login", "status"])
        self.assertEqual(captured["env"]["CODEX_HOME"], os.path.join(tmp, ".codex"))
        self.assertEqual(captured["env"]["HOME"], tmp)
        # Not signed in -> clean UserError carrying the CLI's detail.
        _Proc.returncode = 1
        _Proc.stderr = "Not logged in"
        with patch.object(codex_cli_transport, "resolve_cli_binary", return_value="/usr/bin/codex"), \
             patch.object(codex_cli_transport.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(UserError) as cm:
                acc.action_validate()
        self.assertIn("Not logged in", str(cm.exception))

    def test_codex_wizard_dispatch_and_token_wipe(self):
        acc, _tmp = self._linked_cli_account(name="CodexWiz", provider="openai")
        wiz = self.env["era.ai.account.login"].with_context(
            default_account_id=acc.id).create({})
        # No Claude OAuth state is minted for an OpenAI account.
        self.assertFalse(wiz.authorize_url)
        wiz.auth_json = json.dumps(self._codex_auth_json())
        wiz.action_complete()
        self.assertTrue(acc._cli_is_linked())
        # The pasted tokens must not survive in the transient table.
        self.assertFalse(wiz.auth_json)

    def test_sync_models_revives_archived_rows(self):
        # Regression: archived rows must stay visible to the upsert, otherwise
        # re-syncing trips the unique constraint and aborts the whole sync.
        acc = self.Account.create({
            "name": "CodexResync", "provider": "openai", "auth_mode": "cli_proxy",
        })
        acc.action_sync_models()
        first = era_ai_account.CODEX_CLI_MODELS[0][0]
        row = acc.with_context(active_test=False).model_ids.filtered(
            lambda m: m.model_id == first)
        row.active = False
        acc.action_sync_models()  # must not raise, and must reactivate the row
        self.assertTrue(row.active)

    def test_provider_switch_drops_old_credentials(self):
        acc, tmp = self._linked_cli_account(name="SwitchCreds", provider="anthropic")
        acc.sudo()._cli_write_credentials({"claudeAiOauth": {"accessToken": "tok"}})
        self.assertTrue(acc._cli_is_linked())
        claude_creds = os.path.join(tmp, ".claude", ".credentials.json")
        self.assertTrue(os.path.exists(claude_creds))
        acc.provider = "openai"
        # The stale Claude tokens are gone, not stranded until a switch-back.
        self.assertFalse(os.path.exists(claude_creds))
        self.assertFalse(acc._cli_is_linked())

    def test_cli_proxy_accounts_refuse_non_chat_models(self):
        # The CLI proxies are text-only: image/embedding rows must be refused at
        # save time (they would show up in other modules' image pickers but fail
        # on every call — the era_seo_suite missing-cover trap).
        cli = self.Account.create({
            "name": "CodexNoImg", "provider": "openai", "auth_mode": "cli_proxy",
        })
        with self.assertRaises(ValidationError):
            self.env["era.ai.model"].create({
                "account_id": cli.id, "model_id": "gpt-image-2", "kind": "image",
            })
        with self.assertRaises(ValidationError):
            self.env["era.ai.model"].create({
                "account_id": cli.id, "model_id": "text-embedding-3-small", "kind": "embedding",
            })
        # Chat rows are fine on CLI accounts; image rows are fine on key accounts.
        self.env["era.ai.model"].create({
            "account_id": cli.id, "model_id": "gpt-5.4", "kind": "chat",
        })
        key = self.Account.create({
            "name": "KeyImg", "provider": "openai", "auth_mode": "api_key", "secret": "sk",
        })
        img = self.env["era.ai.model"].create({
            "account_id": key.id, "model_id": "gpt-image-1", "kind": "image",
        })
        # Flipping the key account to cli_proxy archives its non-chat rows
        # (mirrors what a model sync would do) instead of leaving a trap.
        key.auth_mode = "cli_proxy"
        self.assertFalse(img.active)

    def test_cli_extra_args_validated_at_save(self):
        with self.assertRaises(ValidationError):
            self.Account.create({
                "name": "BadArgs", "provider": "openai", "auth_mode": "cli_proxy",
                "cli_extra_args": '--add-dir "/tmp',  # unbalanced quote
            })

    def test_codex_jsonl_last_message_wins_and_fallbacks(self):
        stdout = "\n".join([
            '{"type":"item.completed","item":{"type":"agent_message","text":"draft"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}',
        ])
        self.assertEqual(codex_cli_transport._parse_codex_jsonl(stdout, 0, ""), "final")
        # Plain-text stdout from a hypothetical non-JSON version is still an answer.
        self.assertEqual(codex_cli_transport._parse_codex_jsonl("plain answer", 0, ""),
                         "plain answer")
        # Empty output -> clear error.
        with self.assertRaises(UserError):
            codex_cli_transport._parse_codex_jsonl("", 0, "")
        # Non-zero exit with no events -> stderr detail surfaced.
        with self.assertRaises(UserError) as cm:
            codex_cli_transport._parse_codex_jsonl("", 2, "boom from stderr")
        self.assertIn("boom from stderr", str(cm.exception))

    def test_codex_link_with_auth_json(self):
        acc, tmp = self._linked_cli_account(name="CodexLink", provider="openai")
        acc._codex_link_with_auth_json(json.dumps(self._codex_auth_json(plan="pro")))
        creds_path = os.path.join(tmp, ".codex", "auth.json")
        self.assertTrue(os.path.exists(creds_path))
        with open(creds_path) as f:
            stored = json.load(f)
        self.assertEqual(stored["tokens"]["refresh_token"], "v1.M-rt-456")
        acc.invalidate_recordset(["cli_oauth_linked", "cli_oauth_label"])
        self.assertTrue(acc.cli_oauth_linked)
        self.assertIn("ChatGPT", acc.cli_oauth_label)
        self.assertIn("pro", acc.cli_oauth_label)
        # The transport config now routes through the managed CODEX_HOME.
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["provider"], "openai")
        self.assertEqual(cfg["home_dir"], tmp)
        self.assertEqual(cfg["config_dir"], os.path.join(tmp, ".codex"))
        # Disconnect removes the stored file.
        acc.action_ai_claude_logout()
        self.assertFalse(acc._cli_is_linked())
        self.assertFalse(os.path.exists(creds_path))

    def test_codex_link_rejects_bad_payloads(self):
        acc, _tmp = self._linked_cli_account(name="CodexBad", provider="openai")
        with self.assertRaises(UserError):  # empty
            acc._codex_link_with_auth_json("")
        with self.assertRaises(UserError):  # not JSON
            acc._codex_link_with_auth_json("not json at all")
        with self.assertRaises(UserError):  # API-key file, not a ChatGPT login
            acc._codex_link_with_auth_json(json.dumps({"OPENAI_API_KEY": "sk-x"}))
        with self.assertRaises(UserError):  # explicit non-chatgpt auth mode
            acc._codex_link_with_auth_json(json.dumps(
                self._codex_auth_json(auth_mode="apikey")))
        with self.assertRaises(UserError):  # missing refresh token
            bad = self._codex_auth_json()
            del bad["tokens"]["refresh_token"]
            acc._codex_link_with_auth_json(json.dumps(bad))
        self.assertFalse(acc._cli_is_linked())
        # Linking is manager-only, like the Claude OAuth flow.
        user = new_test_user(self.env, login="era_codex_nomgr", groups="base.group_user")
        with self.assertRaises(UserError):
            acc.with_user(user)._codex_link_with_auth_json(
                json.dumps(self._codex_auth_json()))
        # And only applies to OpenAI CLI-proxy accounts.
        claude_acc, _t = self._linked_cli_account(name="NotCodex", provider="anthropic")
        with self.assertRaises(UserError):
            claude_acc._codex_link_with_auth_json(json.dumps(self._codex_auth_json()))

    def test_codex_parse_device_login_output(self):
        raw = ("\x1b[1mCodex\x1b[0m device login\n"
               "To sign in, visit https://chatgpt.com/codex/device and enter the code:\n"
               "  BLDQ-NRVF\n"
               "Waiting for approval (expires in 15 minutes)...\n")
        url, code = codex_cli_transport.parse_device_login(
            codex_cli_transport._ANSI_RE.sub("", raw))
        self.assertEqual(url, "https://chatgpt.com/codex/device")
        self.assertEqual(code, "BLDQ-NRVF")
        # URL fragments must not be mistaken for the one-time code.
        url2, code2 = codex_cli_transport.parse_device_login(
            "go to https://example.com/device?x=ABCD-EFGH first")
        self.assertEqual(url2, "https://example.com/device?x=ABCD-EFGH")
        self.assertEqual(code2, "")

    def test_codex_device_login_flow(self):
        acc, tmp = self._linked_cli_account(name="CodexDevice", provider="openai")
        out_path = os.path.join(tmp, ".codex", "device_login.out")

        def fake_start(cfg):
            self.assertEqual(cfg["config_dir"], os.path.join(tmp, ".codex"))
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w") as fh:
                fh.write("Visit https://chatgpt.com/codex/device and enter code BLDQ-NRVF\n")
            return 12345, out_path

        with patch.object(codex_cli_transport, "device_login_start", side_effect=fake_start), \
             patch.object(codex_cli_transport, "pid_is_pending_login", return_value=True), \
             patch.object(era_ai_account.time, "sleep"):
            info = acc._codex_device_login_start()
        self.assertEqual(info["url"], "https://chatgpt.com/codex/device")
        self.assertEqual(info["code"], "BLDQ-NRVF")
        self.assertTrue(acc._codex_device_state().get("pid"))

        # Still pending while the codex process lives and no auth.json exists.
        with patch.object(codex_cli_transport, "pid_is_pending_login", return_value=True):
            self.assertEqual(acc._codex_device_login_status(), "pending")
        # Process gone without credentials -> failed, state cleared.
        with patch.object(codex_cli_transport, "pid_is_pending_login", return_value=False):
            self.assertTrue(acc._codex_device_login_status().startswith("failed:"))
        self.assertFalse(acc._codex_device_state())

        # Approval: codex wrote auth.json -> linked, state cleared.
        with patch.object(codex_cli_transport, "device_login_start", side_effect=fake_start), \
             patch.object(codex_cli_transport, "pid_is_pending_login", return_value=True), \
             patch.object(era_ai_account.time, "sleep"):
            acc._codex_device_login_start()
        acc.sudo()._cli_write_credentials(self._codex_auth_json())
        self.assertEqual(acc._codex_device_login_status(), "linked")
        self.assertFalse(acc._codex_device_state())

    def test_codex_device_login_failure_surfaces_output(self):
        acc, tmp = self._linked_cli_account(name="CodexDevFail", provider="openai")
        out_path = os.path.join(tmp, ".codex", "device_login.out")

        def fake_start(cfg):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w") as fh:
                fh.write("error: device code authentication is not enabled\n")
            return 12345, out_path

        with patch.object(codex_cli_transport, "device_login_start", side_effect=fake_start), \
             patch.object(codex_cli_transport, "pid_is_pending_login", return_value=False), \
             patch.object(era_ai_account.time, "sleep"):
            with self.assertRaises(UserError) as cm:
                acc._codex_device_login_start()
        self.assertIn("not enabled", str(cm.exception))
        self.assertFalse(acc._codex_device_state())

    def test_agent_routes_through_codex_account(self):
        acc = self.Account.create({
            "name": "Codex route", "provider": "openai", "auth_mode": "cli_proxy",
        })
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "Codex routed", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        with patch.object(codex_cli_transport, "cli_complete", return_value="codex answer") as mocked:
            out = agent._generate_response("hello")
        self.assertEqual(out, ["codex answer"])
        self.assertTrue(mocked.called)
        self.assertEqual(mocked.call_args.args[0]["provider"], "openai")
        self.assertEqual(mocked.call_args.args[1], era_ai_account.CODEX_CLI_MODELS[0][0])

    def test_codex_cli_proxy_refuses_images(self):
        acc = self.Account.create({
            "name": "Codex img", "provider": "openai", "auth_mode": "cli_proxy",
        })
        with self.assertRaises(UserError):
            acc.generate_image("a cat")

    def test_codex_lock_pool_independent_from_claude(self):
        if llm_cli_transport.fcntl is None:
            self.skipTest("fcntl unavailable")
        # Holding the (only) Claude slot must not block a Codex call: each
        # provider has its own lock-file namespace.
        with llm_cli_transport._global_slot(1, 5):
            with llm_cli_transport._global_slot(
                    1, 0, lock_name=codex_cli_transport._LOCK_SLOT):
                pass

    # --------------------------------------------- tool calling over CLI proxy
    @staticmethod
    def _fake_tools(record):
        """Upstream-shaped tools dict: {name: (desc, allow_end, callable, schema)}."""
        def run(arguments=None):
            record.append(arguments)
            return "result:%s" % (arguments or {}).get("q"), None
        return {
            "get_data": (
                "Fetch data from the database", False, run,
                {"type": "object", "properties": {"q": {"type": "integer"}},
                 "required": ["q"]},
            ),
        }

    def test_cli_tool_loop_roundtrip(self):
        # Round 1: the model calls a tool via the JSON envelope; the upstream
        # request_llm loop executes it; round 2 sees the result and answers.
        acc = self.Account.create({
            "name": "ToolLoop", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        calls, prompts = [], []

        def fake_cli(cfg, model, system_prompt, user_prompt, timeout=180):
            prompts.append((system_prompt, user_prompt))
            if len(prompts) == 1:
                return '{"tool_calls": [{"name": "get_data", "arguments": {"q": 7}}]}'
            return "final answer using the data"

        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env,
            provider=acc._service_provider())
        with patch.object(llm_cli_transport, "cli_complete", side_effect=fake_cli):
            out = service.request_llm(
                "opus", ["be helpful"], [],
                inputs=[{"role": "user", "content": "how many?"}],
                tools=self._fake_tools(calls))
        self.assertEqual(out, ["final answer using the data"])
        self.assertEqual(calls, [{"q": 7}])
        # Round 1 advertised the tool protocol; round 2 carried the result.
        self.assertIn("# Tool protocol", prompts[0][0])
        self.assertIn("get_data", prompts[0][0])
        self.assertIn("result:7", prompts[1][0] + prompts[1][1])

    def test_cli_tool_loop_codex_and_unknown_tool(self):
        # Codex transport path + upstream's unknown-tool feedback loop.
        acc = self.Account.create({
            "name": "ToolLoopCx", "provider": "openai", "auth_mode": "cli_proxy",
        })
        calls, prompts = [], []

        def fake_cli(cfg, model, system_prompt, user_prompt, timeout=180):
            prompts.append((system_prompt, user_prompt))
            if len(prompts) == 1:
                return '{"tool_call": {"name": "nope", "arguments": {}}}'
            return "ok"

        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env,
            provider=acc._service_provider())
        with patch.object(codex_cli_transport, "cli_complete", side_effect=fake_cli):
            out = service.request_llm(
                "gpt-5.3-codex", [], [],
                inputs=[{"role": "user", "content": "hi"}],
                tools=self._fake_tools(calls))
        self.assertEqual(out, ["ok"])
        self.assertFalse(calls)  # the unknown tool never executed
        self.assertIn("unknown tool", prompts[1][0] + prompts[1][1])

    def test_cli_tool_envelope_parser(self):
        from odoo.addons.era_ai_accounts.models import llm_service_patch as p
        # Batched form, single form, fenced form.
        self.assertEqual(
            p._parse_cli_tool_calls('{"tool_calls": [{"name": "a", "arguments": {"x": 1}}]}'),
            [("a", {"x": 1})])
        self.assertEqual(
            p._parse_cli_tool_calls('{"tool_call": {"name": "b"}}'), [("b", {})])
        self.assertEqual(
            p._parse_cli_tool_calls('```json\n{"tool_calls": [{"name": "c", "arguments": {}}]}\n```'),
            [("c", {})])
        # Plain answers pass through; malformed envelopes ask for a retry.
        self.assertIsNone(p._parse_cli_tool_calls("just a normal answer"))
        self.assertEqual(p._parse_cli_tool_calls('{"tool_calls": [{"broken": '), "retry")

    def test_cli_tool_malformed_envelope_gets_one_retry(self):
        acc = self.Account.create({
            "name": "ToolRetry", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        calls, prompts = [], []

        def fake_cli(cfg, model, system_prompt, user_prompt, timeout=180):
            prompts.append(user_prompt)
            if len(prompts) == 1:
                return '{"tool_calls": [{"name": "get_data", '  # truncated JSON
            return "recovered plain answer"

        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env,
            provider=acc._service_provider())
        with patch.object(llm_cli_transport, "cli_complete", side_effect=fake_cli):
            out = service.request_llm(
                "opus", [], [], inputs=[{"role": "user", "content": "q"}],
                tools=self._fake_tools(calls))
        self.assertEqual(out, ["recovered plain answer"])
        self.assertEqual(len(prompts), 2)
        self.assertIn("not a valid tool-call envelope", prompts[1])

    def test_cli_tool_arguments_normalized_to_schema(self):
        # Live regression: Ask AI's read_group crashed on groupby=None because
        # the model omitted the optional array param. Missing/null array params
        # must become [] (what strict-mode OpenAI models send), and loosely
        # typed values must be coerced to the declared type.
        acc = self.Account.create({
            "name": "ToolNorm", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        seen = []

        def run(arguments=None):
            seen.append(arguments)
            return "ok", None

        tools = {
            "read_group": (
                "Group records", False, run,
                {"type": "object",
                 "properties": {
                     "model_name": {"type": "string"},
                     "groupby": {"type": "array"},
                     "domain": {"type": "array"},
                     "limit": {"type": "integer"},
                 },
                 "required": ["model_name"]},
            ),
        }
        replies = iter([
            '{"tool_calls": [{"name": "read_group", "arguments":'
            ' {"model_name": "res.partner", "limit": "5", "domain": null}}]}',
            "done",
        ])
        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env,
            provider="anthropic_cli")
        with patch.object(llm_cli_transport, "cli_complete",
                          side_effect=lambda *a, **k: next(replies)):
            out = service.request_llm(
                "opus", [], [], inputs=[{"role": "user", "content": "q"}], tools=tools)
        self.assertEqual(out, ["done"])
        self.assertEqual(seen, [{
            "model_name": "res.partner",
            "groupby": [],   # omitted array -> []
            "domain": [],    # explicit null array -> []
            "limit": 5,      # string -> integer
        }])

    def test_cli_tools_enabled_gate(self):
        # The account-level switch controls whether agents pass tools at all.
        acc = self.Account.create({
            "name": "ToolGate", "provider": "openai", "auth_mode": "cli_proxy",
        })
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "Gated", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        seen = {}

        def fake_request_llm(self2, model, sys_p, user_p, tools=None, **kw):
            seen["tools"] = tools
            return ["answer"]

        with patch.object(LLMApiService, "request_llm", fake_request_llm):
            agent._generate_response("hello")
        self.assertIsNotNone(seen["tools"], "tools must flow for CLI accounts by default")
        acc.cli_tools_enabled = False
        with patch.object(LLMApiService, "request_llm", fake_request_llm):
            agent._generate_response("hello")
        self.assertIsNone(seen["tools"])

    def test_cli_build_tool_call_response(self):
        # Upstream raises NotImplementedError for unknown providers — the CLI
        # tokens must return the OpenAI-style envelope _flatten understands.
        acc = self.Account.create({
            "name": "ToolEnv", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env,
            provider="anthropic_cli")
        out = service._build_tool_call_response("id1", "value")
        self.assertEqual(out, {"type": "function_call_output",
                               "call_id": "id1", "output": "value"})

    def test_flatten_renders_tool_items(self):
        from odoo.addons.era_ai_accounts.models.llm_service_patch import _flatten
        system, user = _flatten(
            ["sys"], [],
            [
                {"role": "user", "content": "question"},
                {"type": "function_call", "name": "get_data",
                 "call_id": "c1", "arguments": '{"q": 7}'},
                {"type": "function_call_output", "call_id": "c1", "output": "42"},
            ])
        self.assertIn("tool call get_data", system)
        self.assertIn("Result of tool call c1", user)
        self.assertIn("42", user)

    # ------------------------------------------------------------ best practices
    def test_base_url_scheme_constraint(self):
        # http(s) endpoints only — file:// or scheme-less values are refused.
        with self.assertRaises(ValidationError):
            self.Account.create({
                "name": "bad", "provider": "custom", "auth_mode": "api_key",
                "secret": "k", "base_url": "file:///etc/passwd",
            })
        with self.assertRaises(ValidationError):
            self.Account.create({
                "name": "bad2", "provider": "custom", "auth_mode": "api_key",
                "secret": "k", "base_url": "openrouter.ai/api/v1",
            })
        ok = self.Account.create({
            "name": "good", "provider": "custom", "auth_mode": "api_key",
            "secret": "k", "base_url": "https://openrouter.ai/api/v1",
        })
        self.assertTrue(ok)

    def test_sensitive_fields_hidden_from_regular_user(self):
        user = new_test_user(self.env, login="era_lowpriv", groups="base.group_user")
        acc = self.Account.create({
            "name": "Shared3", "provider": "openai", "auth_mode": "api_key",
            "scope": "shared", "secret": "sk-zzz",
        })
        for field in ("secret_is_set", "secret_masked", "cli_extra_args"):
            with self.assertRaises(AccessError, msg=field):
                acc.with_user(user).read([field])

    def test_custom_header_values_sanitized(self):
        # CR/LF in account-sourced header parts must never reach the wire.
        acc = self.Account.create({
            "name": "Hdr", "provider": "custom", "auth_mode": "api_key",
            "secret": "sk-h", "base_url": "https://example.com/v1",
            "auth_header": "Authorization\r\nX-Evil: 1", "referer": "https://r\n.example",
        })
        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env, provider="custom_llm")
        headers = service._get_base_headers()
        for key, value in headers.items():
            self.assertNotIn("\n", key + value)
            self.assertNotIn("\r", key + value)
        self.assertIn("AuthorizationX-Evil: 1", headers)  # collapsed, not split

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

    # ------------------------------------------------------------- Z.AI (GLM)
    def test_zai_service_provider_and_modes(self):
        # Z.AI supports BOTH auth modes (no constraint violation). cli_proxy
        # reuses the Claude (anthropic_cli) transport; api_key uses the "zai"
        # OpenAI-compatible transport.
        cli = self.Account.create({
            "name": "GLM cli", "provider": "zai", "auth_mode": "cli_proxy", "secret": "zk"})
        api = self.Account.create({
            "name": "GLM api", "provider": "zai", "auth_mode": "api_key", "secret": "zk"})
        self.assertEqual(cli._service_provider(), "anthropic_cli")
        self.assertEqual(api._service_provider(), "zai")

    # ---- CLI proxy mode (Claude binary -> Z.AI Anthropic endpoint) ----
    def test_zai_cli_sync_models_and_default(self):
        acc = self.Account.create({
            "name": "GLM cli2", "provider": "zai", "auth_mode": "cli_proxy", "secret": "zk"})
        acc.action_sync_models()
        self.assertEqual(set(acc.model_ids.mapped("model_id")),
                         {m[0] for m in era_ai_account.ZAI_CLI_MODELS})
        self.assertEqual(acc._default_chat_model(), "glm-4.6")
        # CLI is text-only -> all chat rows.
        self.assertEqual(set(acc.model_ids.mapped("kind")), {"chat"})

    def test_zai_cli_cfg_injects_endpoint_and_key(self):
        acc = self.Account.create({
            "name": "GLM cfg", "provider": "zai", "auth_mode": "cli_proxy", "secret": "zk-2"})
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["provider"], "zai")
        self.assertEqual(cfg["anthropic_base_url"], era_ai_account.ZAI_ANTHROPIC_BASE_URL)
        self.assertEqual(cfg["anthropic_auth_token"], "zk-2")

    def test_zai_cli_cfg_requires_key(self):
        acc = self.Account.create({
            "name": "GLM nokey", "provider": "zai", "auth_mode": "cli_proxy"})
        with self.assertRaises(UserError):
            acc._cli_cfg()

    def test_zai_cli_complete_sets_env_and_skips_model(self):
        captured = {}

        class _Proc:
            returncode = 0
            stdout = '{"type":"result","result":"glm via cli","is_error":false}'
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            return _Proc()

        with patch.object(llm_cli_transport, "resolve_cli_binary", return_value="/usr/bin/claude"), \
             patch.object(llm_cli_transport.subprocess, "run", side_effect=fake_run), \
             patch.dict(llm_cli_transport.os.environ, {"ANTHROPIC_API_KEY": "sk-leak"}):
            text = llm_cli_transport.cli_complete(
                {"account_id": 1, "home_dir": "/opt/odoo",
                 "anthropic_base_url": "https://api.z.ai/api/anthropic",
                 "anthropic_auth_token": "zk-token",
                 "min_gap": 0, "gap_per_kb": 0, "lock_wait": 5},
                "glm-4.6", "system here", "user asks", timeout=30)
        self.assertEqual(text, "glm via cli")
        # Z.AI picks the model via env mapping, never via --model.
        self.assertNotIn("--model", captured["args"])
        env = captured["env"]
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.z.ai/api/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "zk-token")
        self.assertEqual(env["ANTHROPIC_DEFAULT_OPUS_MODEL"], "glm-4.6")
        self.assertEqual(env["ANTHROPIC_DEFAULT_SONNET_MODEL"], "glm-4.6")
        self.assertEqual(env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "glm-4.6")
        # The leaked Anthropic key never reaches the subprocess.
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_zai_cli_validate(self):
        acc = self.Account.create({
            "name": "GLM cval", "provider": "zai", "auth_mode": "cli_proxy", "secret": "zk"})
        seen = {}

        class _Proc:
            returncode = 0
            stdout = "1.2.3 (Claude Code)"
            stderr = ""

        def fake_get(self2, url, headers, timeout):
            seen["url"], seen["headers"] = url, headers
            return {"data": []}

        with patch.object(llm_cli_transport, "resolve_cli_binary", return_value="/usr/bin/claude"), \
             patch.object(era_ai_account.subprocess, "run", return_value=_Proc()), \
             patch.object(type(acc), "_http_get_json", fake_get):
            acc.action_validate()
        self.assertEqual(acc.state, "valid")
        self.assertIn("/api/paas/v4/models", seen["url"])
        self.assertEqual(seen["headers"]["Authorization"], "Bearer zk")

    def test_zai_cli_agent_routes(self):
        acc = self.Account.create({
            "name": "GLM route", "provider": "zai", "auth_mode": "cli_proxy", "secret": "zk"})
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "GLM routed", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        with patch.object(llm_cli_transport, "cli_complete", return_value="glm answer") as mocked:
            out = agent._generate_response("hello")
        self.assertEqual(out, ["glm answer"])
        self.assertEqual(mocked.call_args.args[1], "glm-4.6")  # selected GLM model id
        cfg = mocked.call_args.args[0]
        self.assertEqual(cfg["anthropic_base_url"], era_ai_account.ZAI_ANTHROPIC_BASE_URL)
        self.assertEqual(cfg["anthropic_auth_token"], "zk")

    # ---- API key mode (OpenAI-compatible /chat/completions) ----
    def test_zai_api_sync_models(self):
        acc = self.Account.create({
            "name": "GLM apisync", "provider": "zai", "auth_mode": "api_key", "secret": "zk"})
        acc.action_sync_models()
        ids = set(acc.model_ids.mapped("model_id"))
        self.assertIn("glm-4.6", ids)
        self.assertIn("glm-4.5-flash", ids)
        self.assertEqual(set(acc.model_ids.mapped("kind")), {"chat"})
        self.assertEqual(acc._default_chat_model(), "glm-4.6")
        free = acc.model_ids.filtered(lambda m: m.model_id == "glm-4.5-flash")
        self.assertEqual(free.cost_info, "Free")

    def test_zai_api_validate(self):
        acc = self.Account.create({
            "name": "GLM apival", "provider": "zai", "auth_mode": "api_key", "secret": "zk"})
        seen = {}

        def fake_get(self2, url, headers, timeout):
            seen["url"], seen["headers"] = url, headers
            return {"data": [{"id": "glm-4.6"}]}

        with patch.object(type(acc), "_http_get_json", fake_get):
            acc.action_validate()
        self.assertEqual(acc.state, "valid")
        self.assertIn("/api/paas/v4/models", seen["url"])
        self.assertEqual(seen["headers"]["Authorization"], "Bearer zk")

    def test_zai_api_content_via_agent(self):
        acc = self.Account.create({
            "name": "GLM apichat", "provider": "zai", "auth_mode": "api_key", "secret": "zk-a"})
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "GLM api agent", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        captured = {}

        def fake_request(self, method, endpoint, headers=None, body=None, **kwargs):
            captured["endpoint"], captured["body"], captured["headers"] = endpoint, body, headers
            return {"choices": [{"message": {"content": "glm says hi"}}]}

        with patch.object(LLMApiService, "_request", fake_request):
            out = agent._generate_response("hello")
        self.assertEqual(out, ["glm says hi"])
        self.assertEqual(captured["endpoint"], "/chat/completions")
        self.assertEqual(captured["body"]["model"], "glm-4.6")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer zk-a")

    def test_zai_api_generate_text(self):
        acc = self.Account.create({
            "name": "GLM apitext", "provider": "zai", "auth_mode": "api_key", "secret": "zk-t"})
        acc.action_sync_models()

        def fake_request(self, method, endpoint, headers=None, body=None, **kwargs):
            return {"choices": [{"message": {"content": "hello from glm"}}]}

        with patch.object(LLMApiService, "_request", fake_request):
            text = acc.generate_text("hi", system="be brief")
        self.assertEqual(text, "hello from glm")

    def test_zai_build_messages(self):
        from odoo.addons.era_ai_accounts.models.llm_service_patch import _zai_build_messages
        msgs = _zai_build_messages(
            ["sys"], [],
            [
                {"role": "user", "content": "question"},
                {"type": "function_call", "name": "get_data",
                 "call_id": "c1", "arguments": '{"q": 7}'},
                {"type": "function_call_output", "call_id": "c1", "output": "42"},
            ])
        self.assertEqual(msgs[0], {"role": "system", "content": "sys"})
        self.assertEqual(msgs[1], {"role": "user", "content": "question"})
        self.assertEqual(msgs[2]["role"], "assistant")
        self.assertEqual(msgs[2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(msgs[2]["tool_calls"][0]["function"]["name"], "get_data")
        self.assertEqual(msgs[3], {"role": "tool", "tool_call_id": "c1", "content": "42"})

    def test_zai_api_tool_loop_roundtrip(self):
        # Native OpenAI tool_calls over /chat/completions: round 1 calls a tool,
        # the upstream loop executes it, round 2 sees the tool message and answers.
        acc = self.Account.create({
            "name": "GLM tools", "provider": "zai", "auth_mode": "api_key", "secret": "zk-1"})
        calls, bodies = [], []
        replies = iter([
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "get_data", "arguments": '{"q": 7}'}}]}}]},
            {"choices": [{"message": {"content": "final answer using the data"}}]},
        ])

        def fake_request(self2, method, endpoint, headers=None, body=None, **kw):
            bodies.append(body)
            return next(replies)

        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env, provider="zai")
        with patch.object(LLMApiService, "_request", fake_request):
            out = service.request_llm(
                "glm-4.6", ["be helpful"], [],
                inputs=[{"role": "user", "content": "how many?"}],
                tools=self._fake_tools(calls))
        self.assertEqual(out, ["final answer using the data"])
        self.assertEqual(calls, [{"q": 7}])
        # Round 1 advertised the tool; round 2 carried the result back as a tool message.
        self.assertEqual(bodies[0]["tools"][0]["function"]["name"], "get_data")
        self.assertEqual(bodies[0]["tool_choice"], "auto")
        round2 = bodies[1]["messages"]
        self.assertTrue(any(m.get("role") == "assistant" and m.get("tool_calls") for m in round2))
        self.assertTrue(any(
            m.get("role") == "tool" and "result:7" in (m.get("content") or "")
            for m in round2))

    def test_zai_api_tool_call_dict_arguments(self):
        # A non-conforming gateway may send tool-call arguments as an
        # already-parsed dict (not a JSON string) — they must be preserved,
        # not fed to json.loads (TypeError) and silently dropped to {}.
        acc = self.Account.create({
            "name": "GLM dictargs", "provider": "zai", "auth_mode": "api_key", "secret": "zk"})
        calls = []
        replies = iter([
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "get_data", "arguments": {"q": 9}}}]}}]},
            {"choices": [{"message": {"content": "done"}}]},
        ])

        def fake_request(self2, method, endpoint, headers=None, body=None, **kw):
            return next(replies)

        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env, provider="zai")
        with patch.object(LLMApiService, "_request", fake_request):
            out = service.request_llm(
                "glm-4.6", [], [], inputs=[{"role": "user", "content": "q"}],
                tools=self._fake_tools(calls))
        self.assertEqual(out, ["done"])
        self.assertEqual(calls, [{"q": 9}])  # dict args preserved, not {}

    # ------------------------------------------------------------- new safeguards
    def test_cli_denylist_blocks_dangerous_flags(self):
        acc = self.Account.create({
            "name": "Deny", "provider": "openai", "auth_mode": "cli_proxy",
        })
        for flag in ("--dangerously-skip-permissions", "--yolo", "--add-dir /tmp",
                      "--config web_search=enabled", "-c sandbox_mode=none"):
            with self.assertRaises(ValidationError, msg=f"flag={flag}"):
                acc.cli_extra_args = flag
                acc.flush_recordset()

    def test_cli_denylist_allows_benign_flags(self):
        acc = self.Account.create({
            "name": "Allow", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        acc.cli_extra_args = "--max-tokens 4096 --verbose"
        acc.flush_recordset()  # no ValidationError

    def test_request_count_increments(self):
        acc = self.Account.create({
            "name": "Count", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "a", "secret": "t",
        })
        self.assertEqual(acc.request_count, 0)
        self.assertFalse(acc.last_request_at)
        acc._log_request()
        self.env.flush_all()
        self.assertEqual(acc.request_count, 1)
        acc._log_request()
        self.env.flush_all()
        self.assertEqual(acc.request_count, 2)
        self.assertTrue(acc.last_request_at)

    def test_max_concurrency_is_readonly(self):
        acc = self.Account.create({
            "name": "RO", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        field_meta = self.env["era.ai.account"]._fields["max_concurrency"]
        self.assertTrue(field_meta.readonly,
                        "max_concurrency must be readonly (legacy, use ai.cli_max_concurrency)")
