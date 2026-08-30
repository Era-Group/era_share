import base64
import contextlib
import json
import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.era_ai_accounts.utils import (
    codex_cli_transport,
    crypto,
    kimi_cli_transport,
    llm_cli_transport,
)
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
        # Not "resolves to nothing" — a shared account may legitimately exist in
        # the database. What must hold is that A's personal account is never it.
        self.assertNotEqual(self.Account._resolve_for_user(user_b, provider="anthropic"), acc)

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

    def test_assemblyai_sync_and_region(self):
        acc = self.Account.create({
            "name": "Assembly EU", "provider": "assemblyai",
            "auth_mode": "api_key", "secret": "assembly-key",
            "assemblyai_region": "eu",
        })
        acc.action_sync_models()
        self.assertEqual(acc._assemblyai_base_url(), "https://api.eu.assemblyai.com")
        self.assertEqual(acc.model_ids.mapped("model_id"), ["universal-2"])
        self.assertEqual(acc.model_ids.kind, "transcription")

    def test_assemblyai_validate_uses_raw_key(self):
        acc = self.Account.create({
            "name": "Assembly", "provider": "assemblyai",
            "auth_mode": "api_key", "secret": "assembly-key",
        })
        with patch.object(type(acc), "_http_get_json", return_value={}) as mocked:
            acc._validate_connection()
        self.assertEqual(mocked.call_args.args[1]["Authorization"], "assembly-key")
        self.assertIn("api.assemblyai.com/v2/transcript?limit=1",
                      mocked.call_args.args[0])

    def test_assemblyai_transcribe_and_delete_remote(self):
        acc = self.Account.create({
            "name": "Assembly audio", "provider": "assemblyai",
            "auth_mode": "api_key", "secret": "assembly-key",
        })
        calls = []

        class _Resp:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def fake_post(url, **kwargs):
            calls.append(("post", url, kwargs))
            if url.endswith("/upload"):
                return _Resp({"upload_url": "https://cdn.example/audio"})
            return _Resp({"id": "tx-123", "status": "queued"})

        def fake_get(url, **kwargs):
            calls.append(("get", url, kwargs))
            return _Resp({"id": "tx-123", "status": "completed", "text": "hello"})

        def fake_delete(url, **kwargs):
            calls.append(("delete", url, kwargs))
            return _Resp({})

        with patch.object(era_ai_account.requests, "post", side_effect=fake_post), \
             patch.object(era_ai_account.requests, "get", side_effect=fake_get), \
             patch.object(era_ai_account.requests, "delete", side_effect=fake_delete):
            text = acc.transcribe(b"raw-audio", language="ar")
        self.assertEqual(text, "hello")
        upload = calls[0]
        self.assertEqual(upload[2]["headers"]["Authorization"], "assembly-key")
        self.assertNotIn("Bearer", upload[2]["headers"]["Authorization"])
        submit = calls[1]
        self.assertEqual(submit[2]["json"]["speech_models"], ["universal-2"])
        self.assertEqual(submit[2]["json"]["language_code"], "ar")
        self.assertTrue(submit[2]["json"]["speaker_labels"])
        self.assertEqual(calls[-1][0], "delete")
        self.assertTrue(calls[-1][1].endswith("/v2/transcript/tx-123"))

    def test_assemblyai_formats_diarized_utterances(self):
        payload = {
            "text": "flat fallback",
            "utterances": [
                {"speaker": "B", "text": "السلام عليكم"},
                {"speaker": "A", "text": "وعليكم السلام"},
                {"speaker": "B", "text": "أرغب في نظام ERP"},
            ],
        }
        self.assertEqual(
            self.Account._assemblyai_transcript_text(payload),
            "Speaker 1: السلام عليكم\n"
            "Speaker 2: وعليكم السلام\n"
            "Speaker 1: أرغب في نظام ERP",
        )
        self.assertEqual(
            self.Account._assemblyai_transcript_text({"text": "plain transcript"}),
            "plain transcript",
        )

    def test_llm_service_routes_transcription_to_assemblyai(self):
        acc = self.Account.create({
            "name": "Assembly route", "provider": "assemblyai",
            "auth_mode": "api_key", "secret": "assembly-key",
        })
        service = LLMApiService(self.env(context={
            **self.env.context, "era_ai_account_id": acc.id,
        }))
        with patch.object(type(acc), "transcribe", return_value="routed") as mocked:
            text = service.get_transcription(b"audio", language="ar")
        self.assertEqual(text, "routed")
        self.assertIsNone(mocked.call_args.kwargs["model"])
        self.assertEqual(mocked.call_args.kwargs["language"], "ar")

    def test_assemblyai_cannot_be_assigned_to_chat_agent(self):
        acc = self.Account.create({
            "name": "Assembly transcription only", "provider": "assemblyai",
            "auth_mode": "api_key", "secret": "assembly-key",
        })
        with self.assertRaises(ValidationError):
            self.env["ai.agent"].create({
                "name": "Invalid Assembly chat", "llm_model": "gpt-4o",
                "era_account_id": acc.id,
            })

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

    def test_stale_link_still_routes_to_managed_dir(self):
        # Access token lapsed/emptied but a refresh token remains: the account
        # is still "linked" and must keep using the managed dir so the CLI can
        # renew it in place — NOT silently drop to the server's ambient login.
        acc, tmp = self._linked_cli_account()
        acc.sudo()._cli_write_credentials(
            {"claudeAiOauth": {"accessToken": "", "refreshToken": "rt", "expiresAt": 1}})
        self.assertEqual(acc._cli_link_state(), "stale")
        self.assertTrue(acc._cli_is_linked())
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["home_dir"], tmp)
        self.assertEqual(cfg["config_dir"], os.path.join(tmp, ".claude"))

    def test_expired_link_fails_loudly_and_does_not_borrow_ambient(self):
        # A link that was established and then died must not silently fall back
        # to the server's own login (that hides the dead link and spends the
        # wrong subscription). Strict mode (default) raises so a manager re-links.
        acc, tmp = self._linked_cli_account()
        acc.sudo()._cli_write_credentials(
            {"claudeAiOauth": {"accessToken": "", "refreshToken": "", "subscriptionType": "max"}})
        self.assertEqual(acc._cli_link_state(), "expired")
        self.assertFalse(acc._cli_is_linked())
        acc.invalidate_recordset(["cli_link_state", "cli_oauth_linked", "cli_oauth_label"])
        self.assertEqual(acc.cli_link_state, "expired")
        self.assertFalse(acc.cli_oauth_linked)
        with self.assertRaises(UserError):
            acc._cli_cfg()
        # Escape hatch: sites that want zero-downtime while a re-link is pending
        # can opt back into the ambient fall-back.
        self.env["ir.config_parameter"].sudo().set_param(
            "era_ai_accounts.cli_link_strict", "False")
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["home_dir"], "/opt/odoo")
        self.assertFalse(cfg["config_dir"])

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

    def test_context_account_overrides_agent_account(self):
        claude = self.Account.create({
            "name": "Stored route", "provider": "anthropic", "auth_mode": "cli_proxy",
        })
        claude.action_sync_models()
        codex = self.Account.create({
            "name": "Context route", "provider": "openai", "auth_mode": "cli_proxy",
        })
        codex.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "Context override", "llm_model": "gpt-4o",
            "era_account_id": claude.id,
            "era_model_id": claude._default_chat_model_record().id,
        })
        with patch.object(codex_cli_transport, "cli_complete",
                          return_value="context answer") as mocked:
            out = agent.with_context(era_ai_account_id=codex.id)._generate_response("hello")
        self.assertEqual(out, ["context answer"])
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

    # --------------------------------------------------- Kimi (Moonshot AI)
    def test_kimi_service_provider_and_modes(self):
        # Kimi supports BOTH auth modes. cli_proxy drives its own first-party
        # `kimi` binary (kimi_cli); api_key uses the shared OpenAI-compatible
        # transport under the "kimi" token.
        cli = self.Account.create({
            "name": "Kimi cli", "provider": "kimi", "auth_mode": "cli_proxy"})
        api = self.Account.create({
            "name": "Kimi api", "provider": "kimi", "auth_mode": "api_key", "secret": "mk"})
        self.assertEqual(cli._service_provider(), "kimi_cli")
        self.assertEqual(api._service_provider(), "kimi")

    # ---- CLI proxy mode (the `kimi` binary in print mode) ----
    def test_kimi_cli_sync_models_and_default(self):
        acc = self.Account.create({
            "name": "Kimi cli2", "provider": "kimi", "auth_mode": "cli_proxy"})
        acc.action_sync_models()
        self.assertEqual(set(acc.model_ids.mapped("model_id")),
                         {m[0] for m in era_ai_account.KIMI_CLI_MODELS})
        self.assertEqual(acc._default_chat_model(), "kimi-k2.6")
        # CLI is text-only -> all chat rows.
        self.assertEqual(set(acc.model_ids.mapped("kind")), {"chat"})

    def test_kimi_cli_cfg_injects_key_endpoint_and_managed_home(self):
        acc = self.Account.create({
            "name": "Kimi cfg", "provider": "kimi", "auth_mode": "cli_proxy",
            "secret": "mk-2"})
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["provider"], "kimi")
        self.assertEqual(cfg["kimi_api_key"], "mk-2")
        self.assertEqual(cfg["kimi_base_url"], kimi_cli_transport.KIMI_DEFAULT_BASE_URL)
        # A managed KIMI_CODE_HOME is always provided (it holds the tool fence
        # and SYSTEM.md), never the server's own ~/.kimi-code.
        self.assertTrue(cfg["config_dir"].endswith("/.kimi-code"))
        self.assertNotIn("/opt/odoo/.kimi-code", cfg["config_dir"])

    def test_kimi_cli_cfg_requires_key(self):
        # `kimi login` is an interactive device-code flow, so an API key is the
        # only way to drive the CLI from Odoo — fail early and clearly.
        acc = self.Account.create({
            "name": "Kimi nokey", "provider": "kimi", "auth_mode": "cli_proxy"})
        with self.assertRaises(UserError):
            acc._cli_cfg()

    def test_kimi_cli_never_probes_another_providers_credentials(self):
        # Kimi has a managed config dir but no credential file and no in-app
        # subscription link: the link machinery must answer 'none' rather than
        # falling through to the Anthropic credential layout.
        acc = self.Account.create({
            "name": "Kimi link", "provider": "kimi", "auth_mode": "cli_proxy",
            "secret": "mk"})
        info = acc._cli_oauth_info()
        self.assertEqual(info["state"], "none")
        self.assertFalse(info["linked"])
        self.assertEqual(acc.cli_link_state, "none")
        self.assertFalse(acc.cli_oauth_linked)

    def test_kimi_cli_complete_is_fenced_and_parses(self):
        """The invocation verified live against kimi-code 0.36.1."""
        captured = {}
        home = tempfile.mkdtemp(prefix="era_kimi_home_")
        self.addCleanup(shutil.rmtree, home, True)

        class _Proc:
            returncode = 0
            stdout = (
                '{"role":"meta","type":"system.version","version":"0.36.1"}\n'
                '{"role":"assistant","content":"kimi says hi"}\n'
                '{"role":"meta","type":"session.resume_hint","content":"resume me"}\n'
            )
            stderr = "kimi version 0.36.1\n"

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            captured["cwd"] = kwargs.get("cwd")
            captured["workdir_entries"] = os.listdir(kwargs.get("cwd"))
            captured["config"] = open(os.path.join(home, "config.toml")).read()
            captured["system_md"] = open(os.path.join(home, "SYSTEM.md")).read()
            return _Proc()

        with patch.object(kimi_cli_transport, "resolve_cli_binary", return_value="/usr/bin/kimi"), \
             patch.object(kimi_cli_transport.subprocess, "run", side_effect=fake_run), \
             patch.dict(kimi_cli_transport.os.environ,
                        {"KIMI_MODEL_API_KEY": "leaked", "OPENAI_API_KEY": "sk-leak"}):
            text = kimi_cli_transport.cli_complete(
                {"account_id": 1, "home_dir": "/opt/odoo", "config_dir": home,
                 "kimi_api_key": "mk-token",
                 "kimi_base_url": "https://api.moonshot.ai/v1",
                 "min_gap": 0, "gap_per_kb": 0, "lock_wait": 5},
                "kimi-k2.6", "system here", "user asks", timeout=30)

        # Only the assistant events form the answer; meta lines are dropped.
        self.assertEqual(text, "kimi says hi")
        args = captured["args"]
        # Prompt mode: the text is an argv entry (stdin is rejected by the CLI).
        self.assertEqual(args[args.index("-p") + 1], "user asks")
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        # The tool fence lives in config.toml — there is no --config flag.
        self.assertIn(kimi_cli_transport._NO_TOOLS_SENTINEL, captured["config"])
        self.assertIn("max_steps_per_turn = 1", captured["config"])
        # The system prompt replaces the built-in coding-agent persona.
        self.assertEqual(captured["system_md"], "system here")
        # The workspace fence: a fresh empty dir, also the cwd (no --work-dir).
        self.assertEqual(captured["workdir_entries"], [])
        self.assertNotIn("--work-dir", args)
        self.assertFalse(os.path.exists(captured["cwd"]), "work dir must be cleaned up")
        # The model is selected by env, never by -m (which wants a config alias).
        self.assertNotIn("-m", args)
        env = captured["env"]
        self.assertEqual(env["KIMI_MODEL_NAME"], "kimi-k2.6")
        self.assertEqual(env["KIMI_MODEL_API_KEY"], "mk-token")
        self.assertEqual(env["KIMI_MODEL_BASE_URL"], "https://api.moonshot.ai/v1")
        self.assertEqual(env["KIMI_MODEL_PROVIDER_TYPE"], "openai")
        self.assertEqual(env["KIMI_CODE_HOME"], home)
        self.assertEqual(env["KIMI_CLI_NO_AUTO_UPDATE"], "1")
        # An operator's ambient key never reaches the subprocess.
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_kimi_cli_default_system_prompt_when_none_given(self):
        # With no SYSTEM.md the CLI falls back to its built-in coding-agent
        # persona, which is wrong for chat — always write one.
        home = tempfile.mkdtemp(prefix="era_kimi_home_")
        self.addCleanup(shutil.rmtree, home, True)
        kimi_cli_transport._write_runtime_files(home, "")
        self.assertEqual(open(os.path.join(home, "SYSTEM.md")).read(),
                         kimi_cli_transport._DEFAULT_SYSTEM_PROMPT)
        # Regenerated every call, so an edited file cannot weaken the fence.
        with open(os.path.join(home, "config.toml"), "w") as fh:
            fh.write("[tools]\nenabled = []\n")
        kimi_cli_transport._write_runtime_files(home, "sys")
        self.assertIn(kimi_cli_transport._NO_TOOLS_SENTINEL,
                      open(os.path.join(home, "config.toml")).read())

    def test_kimi_cli_rejects_oversized_prompt(self):
        # The CLI only accepts a prompt as an argv entry, which Linux caps at
        # 128 KiB (verified: 127 KB ok, 130 KB -> E2BIG). Fail with a clear
        # message instead of an OSError from the fork.
        home = tempfile.mkdtemp(prefix="era_kimi_home_")
        self.addCleanup(shutil.rmtree, home, True)
        with patch.object(kimi_cli_transport, "resolve_cli_binary", return_value="/usr/bin/kimi"):
            with self.assertRaises(UserError) as err:
                kimi_cli_transport.cli_complete(
                    {"home_dir": "/opt/odoo", "config_dir": home, "kimi_api_key": "mk",
                     "min_gap": 0, "gap_per_kb": 0, "lock_wait": 5},
                    "kimi-k2.6", "", "x" * (200 * 1024), timeout=30)
        self.assertIn("too large", str(err.exception))

    def test_kimi_cli_is_always_single_slot(self):
        captured = {}

        class _Proc:
            returncode = 0
            stdout = '{"role":"assistant","content":"ok"}'
            stderr = ""

        def fake_slot(slots, wait, lock_name=None):
            captured["slots"] = slots
            captured["lock_name"] = lock_name
            return contextlib.nullcontext()

        home = tempfile.mkdtemp(prefix="era_kimi_home_")
        self.addCleanup(shutil.rmtree, home, True)
        with patch.object(kimi_cli_transport, "resolve_cli_binary", return_value="/usr/bin/kimi"), \
             patch.object(kimi_cli_transport.subprocess, "run", return_value=_Proc()), \
             patch.object(kimi_cli_transport, "_global_slot", fake_slot):
            kimi_cli_transport.cli_complete(
                {"home_dir": "/opt/odoo", "config_dir": home, "concurrency": 8,
                 "kimi_api_key": "mk", "min_gap": 0, "gap_per_kb": 0, "lock_wait": 5},
                "kimi-k2.6", "", "hi", timeout=30)
        # SYSTEM.md/config.toml are single-writer per account: concurrency=8 in
        # the settings must NOT widen this pool.
        self.assertEqual(captured["slots"], 1)
        # Its own lock namespace: a Kimi call never queues behind Claude/Codex.
        self.assertEqual(captured["lock_name"], kimi_cli_transport._LOCK_SLOT)
        self.assertNotEqual(kimi_cli_transport._LOCK_SLOT, llm_cli_transport._LOCK_SLOT)
        self.assertNotEqual(kimi_cli_transport._LOCK_SLOT, codex_cli_transport._LOCK_SLOT)

    def test_kimi_binary_resolution_order(self):
        # Odoo's service PATH rarely carries a user bin dir, so the glob list is
        # the real discovery path — it must cover BOTH distributions' install
        # locations (Kimi Code's ~/.kimi-code/bin and kimi-cli's ~/.local/bin).
        globs = kimi_cli_transport._KIMI_GLOBS
        self.assertTrue(any(g.endswith("/.kimi-code/bin/kimi") for g in globs))
        self.assertTrue(any(g.endswith("/.local/bin/kimi") for g in globs))
        with tempfile.TemporaryDirectory() as tmp:
            binary = os.path.join(tmp, "kimi")
            with open(binary, "w") as fh:
                fh.write("#!/bin/sh\n")
            # An explicit override always wins, even over a PATH hit.
            with patch.object(kimi_cli_transport.shutil, "which",
                              return_value="/usr/bin/kimi"):
                self.assertEqual(
                    kimi_cli_transport.resolve_cli_binary(binary), binary)
                # ERA_AI_KIMI_BIN is the next-highest precedence.
                with patch.dict(kimi_cli_transport.os.environ,
                                {"ERA_AI_KIMI_BIN": binary}):
                    self.assertEqual(kimi_cli_transport.resolve_cli_binary(), binary)
                # Then PATH.
                self.assertEqual(kimi_cli_transport.resolve_cli_binary(),
                                 "/usr/bin/kimi")
            # Nothing anywhere -> None (the caller raises a clear UserError).
            with patch.object(kimi_cli_transport.shutil, "which", return_value=None), \
                 patch.object(kimi_cli_transport, "_KIMI_GLOBS", []):
                self.assertIsNone(kimi_cli_transport.resolve_cli_binary())

    def test_kimi_cli_error_output_is_cleaned(self):
        # A failed run exits non-zero with the message on stderr, wrapped in a
        # version banner and a "See log:" footer that must not reach the user
        # (shape verified live on kimi-code 0.36.1).
        stderr = (
            "kimi version 0.36.1\n"
            "error: failed to run prompt: provider.auth_error: 401 Invalid Authentication\n"
            "See log: /opt/odoo/.kimi-code/logs/kimi-code.log\n"
        )
        with self.assertRaises(UserError) as err:
            kimi_cli_transport._parse_kimi_output(
                '{"role":"meta","type":"system.version","version":"0.36.1"}', 1, stderr)
        message = str(err.exception)
        self.assertIn("401 Invalid Authentication", message)
        self.assertNotIn("See log:", message)
        self.assertNotIn("kimi version", message)
        # Exit 0 but no assistant event -> empty answer, not a silent "".
        with self.assertRaises(UserError):
            kimi_cli_transport._parse_kimi_output(
                '{"role":"meta","type":"system.version","version":"0.36.1"}', 0, "")
        # Multiple assistant events are joined in order.
        self.assertEqual(
            kimi_cli_transport._parse_kimi_output(
                '{"role":"assistant","content":"one"}\n'
                '{"role":"meta","type":"x"}\n'
                '{"role":"assistant","content":"two"}\n', 0, ""),
            "one\ntwo")

    def test_kimi_cli_validate_checks_binary_and_key(self):
        acc = self.Account.create({
            "name": "Kimi cval", "provider": "kimi", "auth_mode": "cli_proxy",
            "secret": "mk"})
        seen = {}

        class _Proc:
            returncode = 0
            stdout = "kimi 1.0.0"
            stderr = ""

        def fake_get(self2, url, headers, timeout):
            seen["url"], seen["headers"] = url, headers
            return {"data": []}

        with patch.object(kimi_cli_transport, "resolve_cli_binary", return_value="/usr/bin/kimi"), \
             patch.object(kimi_cli_transport.subprocess, "run", return_value=_Proc()), \
             patch.object(type(acc), "_http_get_json", fake_get):
            acc.action_validate()
        self.assertEqual(acc.state, "valid")
        self.assertEqual(seen["url"], "https://api.moonshot.ai/v1/models")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer mk")

    def test_kimi_cli_validate_fails_without_binary(self):
        # _validate_connection, not action_validate: the latter persists the
        # error through a second cursor, which a TransactionCase cannot provide.
        acc = self.Account.create({
            "name": "Kimi noBin", "provider": "kimi", "auth_mode": "cli_proxy",
            "secret": "mk"})
        with patch.object(kimi_cli_transport, "resolve_cli_binary", return_value=None):
            with self.assertRaises(UserError) as err:
                acc._validate_connection()
        self.assertIn("Kimi CLI was not found", str(err.exception))

    def test_kimi_cli_agent_routes(self):
        acc = self.Account.create({
            "name": "Kimi route", "provider": "kimi", "auth_mode": "cli_proxy",
            "secret": "mk"})
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "Kimi routed", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        with patch.object(kimi_cli_transport, "cli_complete", return_value="kimi answer") as mocked:
            out = agent._generate_response("hello")
        self.assertEqual(out, ["kimi answer"])
        self.assertEqual(mocked.call_args.args[1], "kimi-k2.6")
        cfg = mocked.call_args.args[0]
        self.assertEqual(cfg["kimi_api_key"], "mk")
        self.assertTrue(cfg["config_dir"].endswith("/.kimi-code"))

    def test_kimi_cli_proxy_refuses_non_chat_models(self):
        acc = self.Account.create({
            "name": "Kimi img", "provider": "kimi", "auth_mode": "cli_proxy"})
        with self.assertRaises(ValidationError):
            self.env["era.ai.model"].create({
                "account_id": acc.id, "model_id": "some-image", "kind": "image"})

    def test_kimi_denylist_blocks_cli_capability_flags(self):
        acc = self.Account.create({
            "name": "Kimi deny", "provider": "kimi", "auth_mode": "cli_proxy"})
        for flag in ("--yes", "--auto-approve", "--afk", "--work-dir /opt/odoo",
                     "--agent-file /tmp/a.yaml", "--mcp-config-file /tmp/m.json",
                     "--skills-dir /tmp", "--session abc"):
            with self.assertRaises(ValidationError, msg=f"flag={flag}"):
                acc.cli_extra_args = flag
                acc.flush_recordset()

    # ---- Kimi Code plan (flat monthly subscription) ----
    def _kimi_plan_account(self, name="Kimi plan", **vals):
        return self.Account.create(dict({
            "name": name, "provider": "kimi", "auth_mode": "cli_proxy",
            "kimi_plan": "coding", "secret": "sk-plan",
        }, **vals))

    def test_kimi_coding_plan_endpoint_and_protocol(self):
        # The plan is a different product from the pay-per-token platform: its
        # own host AND its own wire protocol (verified live — the CLI posts to
        # /v1/messages with x-api-key when told the provider is anthropic).
        acc = self._kimi_plan_account()
        cfg = acc._cli_cfg()
        self.assertEqual(cfg["kimi_base_url"], era_ai_account.KIMI_CODING_BASE_URL)
        self.assertEqual(cfg["kimi_provider_type"], "anthropic")
        self.assertEqual(cfg["kimi_api_key"], "sk-plan")
        self.assertTrue(cfg["config_dir"].endswith("/.kimi-code"))
        # The platform account stays OpenAI-shaped on the other host.
        platform = self.Account.create({
            "name": "Kimi pf", "provider": "kimi", "auth_mode": "cli_proxy",
            "secret": "sk-pf"})
        pcfg = platform._cli_cfg()
        self.assertEqual(pcfg["kimi_base_url"], era_ai_account.KIMI_OPENAI_BASE_URL)
        self.assertEqual(pcfg["kimi_provider_type"], "openai")

    def test_kimi_coding_plan_has_its_own_model_ids(self):
        # The plan serves "kimi-for-coding"/"k3", not the platform's
        # "kimi-k2.7-code"/"kimi-k3" — mixing them up 404s every call.
        acc = self._kimi_plan_account("Kimi plan sync")
        acc.action_sync_models()
        ids = set(acc.model_ids.mapped("model_id"))
        self.assertEqual(ids, {m[0] for m in era_ai_account.KIMI_CODING_MODELS})
        self.assertIn("kimi-for-coding", ids)
        self.assertNotIn("kimi-k2.6", ids)
        self.assertEqual(acc._default_chat_model(), "kimi-for-coding")

    def test_kimi_plan_switch_reconciles_the_catalog(self):
        # Switching plans must not leave the other surface's ids selectable.
        acc = self._kimi_plan_account("Kimi switch")
        acc.action_sync_models()
        self.assertIn("kimi-for-coding", acc.model_ids.mapped("model_id"))
        acc.kimi_plan = "platform"
        acc.action_sync_models()
        active = acc.model_ids.filtered("active").mapped("model_id")
        self.assertIn("kimi-k2.6", active)
        self.assertNotIn("kimi-for-coding", active)

    def test_kimi_coding_plan_requires_cli_proxy(self):
        # The plan endpoint is Anthropic-shaped; the account's HTTP transport
        # only speaks OpenAI chat-completions, so it must go through the CLI.
        with self.assertRaises(ValidationError):
            self.Account.create({
                "name": "Kimi plan api", "provider": "kimi",
                "auth_mode": "api_key", "kimi_plan": "coding", "secret": "sk"})

    def test_kimi_coding_plan_validate_hits_plan_host(self):
        acc = self._kimi_plan_account("Kimi plan val")
        seen = {}

        class _Proc:
            returncode = 0
            stdout = "0.36.1"
            stderr = ""

        def fake_get(self2, url, headers, timeout):
            seen["url"], seen["headers"] = url, headers
            return {"data": []}

        with patch.object(kimi_cli_transport, "resolve_cli_binary", return_value="/usr/bin/kimi"), \
             patch.object(kimi_cli_transport.subprocess, "run", return_value=_Proc()), \
             patch.object(type(acc), "_http_get_json", fake_get):
            acc.action_validate()
        self.assertEqual(acc.state, "valid")
        self.assertEqual(seen["url"], "https://api.kimi.com/coding/v1/models")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-plan")

    def test_kimi_coding_plan_agent_routes(self):
        acc = self._kimi_plan_account("Kimi plan route")
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "Kimi plan agent", "llm_model": "gpt-4o",
            "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        with patch.object(kimi_cli_transport, "cli_complete",
                          return_value="plan answer") as mocked:
            out = agent._generate_response("hello")
        self.assertEqual(out, ["plan answer"])
        self.assertEqual(mocked.call_args.args[1], "kimi-for-coding")
        cfg = mocked.call_args.args[0]
        self.assertEqual(cfg["kimi_provider_type"], "anthropic")
        self.assertEqual(cfg["kimi_base_url"], era_ai_account.KIMI_CODING_BASE_URL)

    def test_kimi_context_size_per_model(self):
        # The CLI needs a context window for the env-synthesized model; the 1M
        # models are named differently on each surface.
        self.assertEqual(kimi_cli_transport._context_size("kimi-k3"), 1048576)
        self.assertEqual(kimi_cli_transport._context_size("k3"), 1048576)
        self.assertEqual(kimi_cli_transport._context_size("kimi-for-coding"),
                         kimi_cli_transport.KIMI_DEFAULT_CONTEXT_SIZE)

    def test_kimi_plan_base_url_override_wins(self):
        acc = self._kimi_plan_account(
            "Kimi plan proxy", base_url="https://gateway.internal/coding/v1")
        self.assertEqual(acc._cli_cfg()["kimi_base_url"],
                         "https://gateway.internal/coding/v1")
        # ...but the protocol still follows the selected plan.
        self.assertEqual(acc._cli_cfg()["kimi_provider_type"], "anthropic")

    # ---- Kimi device login (subscription linked in-app) ----
    _LINKED_CONFIG = (
        'default_model = "kimi-for-coding"\n'
        "\n"
        "[providers.kimi-code]\n"
        'type = "kimi"\n'
        'base_url = "https://api.kimi.com/coding/v1"\n'
        "# NB: no api_key here — the CLI rejects a provider carrying both\n"
        "# apiKey and oauth ('they are mutually exclusive', verified live).\n"
        "\n"
        "[providers.kimi-code.oauth]\n"
        'storage = "file"\n'
        'key = "oauth/kimi-code"\n'
        "\n"
        '[models.kimi-for-coding]\n'
        'provider = "kimi-code"\n'
        'model = "kimi-for-coding"\n'
        'display_name = "Kimi K2.7 Code"\n'
        "max_context_size = 262144\n"
        "\n"
        "[models.k3]\n"
        'provider = "kimi-code"\n'
        'model = "k3"\n'
        "max_context_size = 1048576\n"
    )

    def _linked_kimi_account(self, name="Kimi linked"):
        """An account whose managed home already holds a provisioned login."""
        acc = self.Account.create({
            "name": name, "provider": "kimi", "auth_mode": "cli_proxy"})
        home = acc._cli_managed_config_dir(create=True)
        with open(os.path.join(home, "config.toml"), "w") as fh:
            fh.write(self._LINKED_CONFIG)
        self.addCleanup(shutil.rmtree, acc._cli_managed_home(), True)
        acc.invalidate_recordset()
        return acc, home

    def test_kimi_link_detected_from_provisioned_config(self):
        acc, _home = self._linked_kimi_account()
        self.assertTrue(acc.cli_oauth_linked)
        self.assertEqual(acc.cli_link_state, "active")
        # The banner must name Kimi, not fall through to the Claude wording.
        self.assertEqual(acc.cli_oauth_label, "Kimi Code subscription linked")
        self.assertNotIn("Claude", acc.cli_oauth_label)

    def test_kimi_linked_account_needs_no_api_key(self):
        acc, home = self._linked_kimi_account("Kimi linked nokey")
        cfg = acc._cli_cfg()
        self.assertTrue(cfg["kimi_oauth"])
        self.assertNotIn("kimi_api_key", cfg)
        self.assertEqual(cfg["config_dir"], home)

    def test_kimi_linked_models_come_from_the_subscription(self):
        acc, _home = self._linked_kimi_account("Kimi linked models")
        acc.action_sync_models()
        self.assertEqual(set(acc.model_ids.mapped("model_id")),
                         {"k3", "kimi-for-coding"})
        # default_model from the provisioned config wins over any curated list.
        self.assertEqual(acc._default_chat_model(), "kimi-for-coding")

    def test_kimi_linked_call_does_not_inject_the_env_provider(self):
        # Exporting KIMI_MODEL_* would synthesize the private "__kimi_env__"
        # provider AND make it the default model, silently bypassing the linked
        # subscription. A linked account must select the model with -m instead.
        acc, home = self._linked_kimi_account("Kimi linked call")
        captured = {}

        class _Proc:
            returncode = 0
            stdout = '{"role":"assistant","content":"linked answer"}'
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            return _Proc()

        with patch.object(kimi_cli_transport, "resolve_cli_binary", return_value="/usr/bin/kimi"), \
             patch.object(kimi_cli_transport.subprocess, "run", side_effect=fake_run):
            text = kimi_cli_transport.cli_complete(
                dict(acc._cli_cfg(), min_gap=0, gap_per_kb=0, lock_wait=5),
                "kimi-for-coding", "sys", "hi", timeout=30)
        self.assertEqual(text, "linked answer")
        env = captured["env"]
        for var in ("KIMI_MODEL_API_KEY", "KIMI_MODEL_NAME", "KIMI_MODEL_BASE_URL",
                    "KIMI_MODEL_PROVIDER_TYPE"):
            self.assertNotIn(var, env, "%s must not be injected for a linked account" % var)
        self.assertEqual(env["KIMI_CODE_HOME"], home)
        args = captured["args"]
        self.assertEqual(args[args.index("-m") + 1], "kimi-for-coding")

    def test_kimi_call_preserves_the_login_in_config_toml(self):
        # The fence is rewritten before every call; if that rewrite replaced the
        # file, the first call after a login would erase the subscription.
        acc, home = self._linked_kimi_account("Kimi linked merge")
        config_path = os.path.join(home, "config.toml")
        kimi_cli_transport._write_runtime_files(home, "system prompt")
        merged = open(config_path).read()
        # The login's provisioning survives verbatim...
        self.assertIn('[providers.kimi-code.oauth]', merged)
        self.assertIn('key = "oauth/kimi-code"', merged)
        self.assertIn("[models.k3]", merged)
        self.assertIn('default_model = "kimi-for-coding"', merged)
        # ...and the fence is still applied.
        self.assertIn(kimi_cli_transport._NO_TOOLS_SENTINEL, merged)
        self.assertIn("max_steps_per_turn = 1", merged)
        # Still parses, and the account still reads as linked.
        self.assertTrue(kimi_cli_transport.read_link_info(home)["linked"])
        # Repeated calls must not accumulate duplicate [tools] tables.
        kimi_cli_transport._write_runtime_files(home, "system prompt")
        self.assertEqual(open(config_path).read().count("[tools]"), 1)

    def test_kimi_call_overrides_a_tampered_fence(self):
        # An operator (or the CLI itself) widening [tools] must not survive.
        acc, home = self._linked_kimi_account("Kimi linked tamper")
        with open(os.path.join(home, "config.toml"), "a") as fh:
            fh.write('\n[tools]\nenabled = ["Bash", "Write"]\n')
        kimi_cli_transport._write_runtime_files(home, "s")
        merged = open(os.path.join(home, "config.toml")).read()
        self.assertNotIn('"Bash"', merged)
        self.assertIn(kimi_cli_transport._NO_TOOLS_SENTINEL, merged)
        self.assertIn("[providers.kimi-code.oauth]", merged)

    def test_kimi_device_login_flow(self):
        acc = self.Account.create({
            "name": "Kimi dev", "provider": "kimi", "auth_mode": "cli_proxy"})
        self.addCleanup(shutil.rmtree, acc._cli_managed_home(), True)
        output = (
            "\nOpening browser for Kimi device login: "
            "https://www.kimi.com/code/authorize_device?user_code=N9Z1-6I0B\n"
            "If the browser did not open, paste the URL above and enter code: N9Z1-6I0B\n"
            "Code expires in 1800s.\nWaiting for authorization to complete...\n"
        )

        def fake_start(cfg):
            path = os.path.join(cfg["config_dir"], "device_login.out")
            with open(path, "w") as fh:
                fh.write(output)
            return 4242, path

        with patch.object(kimi_cli_transport, "device_login_start", side_effect=fake_start), \
             patch.object(kimi_cli_transport, "pid_is_pending_login", return_value=True):
            info = acc._kimi_device_login_start()
            self.assertEqual(info["code"], "N9Z1-6I0B")
            self.assertIn("authorize_device", info["url"])
            # Not approved yet.
            self.assertEqual(acc._kimi_device_login_status(), "pending")
            # The CLI provisions the link once the manager approves.
            with open(os.path.join(acc._cli_managed_config_dir(), "config.toml"), "w") as fh:
                fh.write(self._LINKED_CONFIG)
            acc.invalidate_recordset()
            self.assertEqual(acc._kimi_device_login_status(), "linked")
        self.assertTrue(acc.cli_oauth_linked)

    def test_kimi_device_login_parser(self):
        url, code = kimi_cli_transport.parse_device_login(
            "Opening browser for Kimi device login: "
            "https://www.kimi.com/code/authorize_device?user_code=YZOF-LTAX\n"
            "Code expires in 1800s.")
        self.assertEqual(url,
                         "https://www.kimi.com/code/authorize_device?user_code=YZOF-LTAX")
        self.assertEqual(code, "YZOF-LTAX")
        self.assertEqual(kimi_cli_transport.parse_device_login(""), ("", ""))

    def test_kimi_unlink_removes_the_provisioned_config(self):
        acc, home = self._linked_kimi_account("Kimi unlink")
        self.assertTrue(acc.cli_oauth_linked)
        acc.action_ai_claude_logout()
        acc.invalidate_recordset()
        self.assertFalse(acc.cli_oauth_linked)
        self.assertFalse(os.path.exists(os.path.join(home, "config.toml")))

    def test_kimi_wizard_routes_to_the_kimi_device_flow(self):
        acc = self.Account.create({
            "name": "Kimi wiz", "provider": "kimi", "auth_mode": "cli_proxy"})
        self.addCleanup(shutil.rmtree, acc._cli_managed_home(), True)
        wizard = self.env["era.ai.account.login"].create({"account_id": acc.id})
        called = {}

        def fake_start():
            called["started"] = True
            return {"url": "https://www.kimi.com/code/authorize_device?user_code=AAAA-BBBB",
                    "code": "AAAA-BBBB", "raw": ""}

        with patch.object(type(acc), "_kimi_device_login_start", side_effect=fake_start):
            wizard.action_device_start()
        self.assertTrue(called.get("started"))
        self.assertEqual(wizard.device_code, "AAAA-BBBB")
        self.assertIn("30 minutes", wizard.device_status)

    # ---- API key mode (OpenAI-compatible /chat/completions) ----
    def test_kimi_api_sync_models(self):
        acc = self.Account.create({
            "name": "Kimi apisync", "provider": "kimi", "auth_mode": "api_key",
            "secret": "mk"})
        acc.action_sync_models()
        ids = set(acc.model_ids.mapped("model_id"))
        self.assertIn("kimi-k3", ids)
        self.assertIn("kimi-k2.6", ids)
        self.assertEqual(set(acc.model_ids.mapped("kind")), {"chat"})
        self.assertEqual(acc._default_chat_model(), "kimi-k2.6")

    def test_kimi_api_validate(self):
        acc = self.Account.create({
            "name": "Kimi apival", "provider": "kimi", "auth_mode": "api_key",
            "secret": "mk"})
        seen = {}

        def fake_get(self2, url, headers, timeout):
            seen["url"], seen["headers"] = url, headers
            return {"data": [{"id": "kimi-k2.6"}]}

        with patch.object(type(acc), "_http_get_json", fake_get):
            acc.action_validate()
        self.assertEqual(acc.state, "valid")
        self.assertEqual(seen["url"], "https://api.moonshot.ai/v1/models")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer mk")

    def test_kimi_api_content_via_agent(self):
        acc = self.Account.create({
            "name": "Kimi apichat", "provider": "kimi", "auth_mode": "api_key",
            "secret": "mk-a"})
        acc.action_sync_models()
        agent = self.env["ai.agent"].create({
            "name": "Kimi api agent", "llm_model": "gpt-4o", "era_account_id": acc.id,
            "era_model_id": acc._default_chat_model_record().id,
        })
        captured = {}

        def fake_request(self2, method, endpoint, headers=None, body=None, **kwargs):
            captured["endpoint"], captured["body"], captured["headers"] = endpoint, body, headers
            return {"choices": [{"message": {"content": "kimi says hi"}}]}

        with patch.object(LLMApiService, "_request", fake_request):
            out = agent._generate_response("hello")
        self.assertEqual(out, ["kimi says hi"])
        self.assertEqual(captured["endpoint"], "/chat/completions")
        self.assertEqual(captured["body"]["model"], "kimi-k2.6")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer mk-a")

    def test_kimi_api_base_url_override(self):
        # China region / gateway: the account's base URL wins over the default.
        acc = self.Account.create({
            "name": "Kimi cn", "provider": "kimi", "auth_mode": "api_key",
            "secret": "mk", "base_url": "https://api.moonshot.cn/v1"})
        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env, provider="kimi")
        self.assertEqual(service.base_url, "https://api.moonshot.cn/v1")

    def test_kimi_api_tool_loop_roundtrip(self):
        acc = self.Account.create({
            "name": "Kimi tools", "provider": "kimi", "auth_mode": "api_key",
            "secret": "mk-1"})
        calls = []
        replies = iter([
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "get_data", "arguments": '{"q": 7}'}}]}}]},
            {"choices": [{"message": {"content": "the answer is 7"}}]},
        ])
        bodies = []

        def fake_request(self2, method, endpoint, headers=None, body=None, **kw):
            bodies.append(body)
            return next(replies)

        service = LLMApiService(
            env=acc.with_context(era_ai_account_id=acc.id).env, provider="kimi")
        with patch.object(LLMApiService, "_request", fake_request):
            out = service.request_llm(
                "kimi-k2.6", [], [], inputs=[{"role": "user", "content": "q"}],
                tools=self._fake_tools(calls))
        self.assertEqual(out, ["the answer is 7"])
        self.assertEqual(calls, [{"q": 7}])
        # Round 2 replays the call + its result as native assistant/tool turns.
        round2 = bodies[1]["messages"]
        self.assertTrue(any(m.get("role") == "assistant" and m.get("tool_calls") for m in round2))
        self.assertTrue(any(
            m.get("role") == "tool" and "result:7" in (m.get("content") or "")
            for m in round2))

    def test_kimi_api_generate_text(self):
        acc = self.Account.create({
            "name": "Kimi apitext", "provider": "kimi", "auth_mode": "api_key",
            "secret": "mk-t"})
        acc.action_sync_models()

        def fake_request(self2, method, endpoint, headers=None, body=None, **kwargs):
            return {"choices": [{"message": {"content": "generated"}}]}

        with patch.object(LLMApiService, "_request", fake_request):
            self.assertEqual(acc.generate_text("write something"), "generated")

    def test_kimi_refuses_images_and_transcription(self):
        acc = self.Account.create({
            "name": "Kimi noimg", "provider": "kimi", "auth_mode": "api_key",
            "secret": "mk"})
        with self.assertRaises(UserError):
            acc.generate_image("a cat")
        with self.assertRaises(UserError):
            acc.transcribe(b"audio-bytes")

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


@tagged("post_install", "-at_install")
class TestVisitorFacingErrors(TransactionCase):
    """What a website visitor is told when the AI provider fails.

    Live chat visitors on a real deployment were shown "The Codex CLI returned
    an empty response." and "Incorrect API key provided: sk-..." — the
    provider's own words, mid-conversation, in English, to Arabic-speaking
    customers who then left. Odoo's own handler hides that from anyone who is
    not internal; this module's override had quietly removed the distinction.
    """

    def setUp(self):
        super().setUp()
        self.agent = self.env["ai.agent"].sudo().create({
            "name": "Site assistant",
            "system_prompt": "Answer questions.",
        })
        self.channel = self.env["discuss.channel"].sudo().create(
            {"name": "Visitor chat"})

    def _guest(self, lang=None):
        guest = self.env["mail.guest"].sudo().create(
            dict({"name": "Visitor #1"}, **({"lang": lang} if lang else {})))
        self.env["discuss.channel.member"].sudo().create({
            "channel_id": self.channel.id, "guest_id": guest.id})
        return guest

    def test_the_visitor_never_sees_the_provider_wording(self):
        self._guest()
        message = self.agent._visitor_error_message(self.channel)
        for leak in ("CLI", "API key", "sk-", "Traceback", "token"):
            self.assertNotIn(leak.lower(), message.lower(),
                             "the provider's wording reached the customer")

    def test_the_visitor_is_asked_how_to_reach_them(self):
        """The chat is lost; the customer is not, as long as we can write.

        Pinned to an English guest so the assertion tests the wording and not
        whichever language the test database happens to have loaded.
        """
        self._guest(lang="en_US")
        message = self.agent._visitor_error_message(self.channel)
        self.assertIn("email", message.lower())

    def test_it_answers_in_the_language_the_guest_is_reading(self):
        arabic = [code for code, __ in self.env["res.lang"].get_installed()
                  if code.startswith("ar")]
        if not arabic:
            self.skipTest("No Arabic language installed here")
        self._guest(lang=arabic[0])
        self.assertEqual(self.agent._visitor_language(self.channel), arabic[0])

    def test_an_internal_user_still_gets_the_technical_detail(self):
        """Whoever is debugging needs the provider's own words."""
        detailed = self.agent._friendly_ai_error_message(
            Exception("AI request failed: Incorrect API key provided"))
        self.assertIn("Incorrect API key", detailed)

    def test_a_guest_without_a_language_still_gets_a_message(self):
        self._guest()
        self.assertTrue(self.agent._visitor_error_message(self.channel))

    def _say(self, text, from_visitor=True, guest=None):
        values = {
            "model": "discuss.channel", "res_id": self.channel.id,
            "body": "<p>%s</p>" % text, "message_type": "comment",
            "subtype_id": self.env.ref("mail.mt_comment").id,
        }
        if from_visitor and guest:
            values["author_guest_id"] = guest.id
        elif not from_visitor:
            values["author_id"] = self.agent.partner_id.id
        return self.env["mail.message"].sudo().create(values)

    def test_arabic_writing_beats_an_english_browser_header(self):
        """The observed failure: the guest's browser said en_US while the
        customer was typing Arabic, so the apology went out in English."""
        arabic = [c for c, __ in self.env["res.lang"].get_installed()
                  if c.startswith("ar")]
        if not arabic:
            self.skipTest("No Arabic language installed here")
        guest = self._guest(lang="en_US")
        self._say("كيف اصدر فاتورة من التطبيق؟", guest=guest)
        self.assertEqual(self.agent._visitor_language(self.channel), arabic[0])

    def test_latin_text_leaves_the_browser_header_alone(self):
        """Latin script says nothing about which Latin language."""
        guest = self._guest(lang="en_US")
        self._say("how do I issue an invoice?", guest=guest)
        self.assertEqual(self.agent._visitor_language(self.channel), "en_US")

    def test_one_borrowed_word_does_not_switch_language(self):
        guest = self._guest(lang="en_US")
        self._say("what does فاتورة mean in your app, exactly? "
                  "I have been reading the docs all morning", guest=guest)
        self.assertEqual(self.agent._visitor_language(self.channel), "en_US")

    def test_our_own_side_is_not_evidence_of_their_language(self):
        """The assistant writes Arabic to everyone; that proves nothing."""
        guest = self._guest(lang="en_US")
        self._say("hello, can you help?", guest=guest)
        self._say("أهلاً بك، كيف أقدر أساعدك اليوم؟", from_visitor=False)
        self.assertEqual(self.agent._visitor_language(self.channel), "en_US")

    def test_an_empty_conversation_falls_back_quietly(self):
        self._guest(lang="en_US")
        self.assertEqual(self.agent._visitor_language(self.channel), "en_US")

    def test_a_bot_operator_without_a_user_is_still_our_side(self):
        """Live chat operators are often bot partners with no user behind
        them. Counting their English error messages as the customer's text
        decided the language against the customer who wrote Arabic."""
        arabic = [c for c, __ in self.env["res.lang"].get_installed()
                  if c.startswith("ar")]
        if "livechat_operator_id" not in self.channel._fields:
            self.skipTest("Live chat is not installed here")
        if not arabic:
            self.skipTest("No Arabic language installed here")
        bot = self.env["res.partner"].sudo().create({"name": "Site bot"})
        self.channel.sudo().livechat_operator_id = bot.id
        guest = self._guest(lang="en_US")
        self._say("محتاج فواتير بدون ضريبة", guest=guest)
        for __ in range(3):
            message = self._say("Codex CLI error: access token could not be "
                                "refreshed, please log out and sign in again",
                                from_visitor=False)
            message.sudo().author_id = bot.id
        self.assertEqual(self.agent._visitor_language(self.channel), arabic[0])


@tagged("post_install", "-at_install")
class TestAccountUsageIsVisible(TransactionCase):
    """An account that has been failing for weeks must not look idle.

    Usage was counted only by this module's image, audio and text helpers —
    never by the agent path, which is what website chat and every scheduled
    agent actually use. Those accounts reported zero requests and no error,
    so nobody could tell a dead one from an unused one.
    """

    def setUp(self):
        super().setUp()
        self.account = self.env["era.ai.account"].sudo().create({
            "name": "Usage", "provider": "cloudflare", "auth_mode": "api_key",
            "cf_account_id": "a", "secret": "t",
        })

    def test_a_successful_call_is_counted_immediately(self):
        self.assertEqual(self.account.request_count, 0)
        self.account._log_request()
        self.assertEqual(self.account.request_count, 1,
                         "the raw UPDATE left a stale value in the ORM cache")
        self.assertTrue(self.account.last_request_at)

    def test_a_failure_is_remembered(self):
        self.account._log_failure(Exception("Incorrect API key provided"))
        self.assertIn("Incorrect API key", self.account.last_error)

    def test_a_success_clears_the_last_failure(self):
        self.account._log_failure(Exception("transient blip"))
        self.assertTrue(self.account.last_error)
        self.account._clear_failure()
        self.assertFalse(self.account.last_error)

    def test_clearing_when_there_is_nothing_to_clear_is_harmless(self):
        self.account._clear_failure()
        self.assertFalse(self.account.last_error)

    def test_a_long_error_is_truncated_rather_than_refused(self):
        self.account._log_failure(Exception("x" * 5000))
        self.assertLessEqual(len(self.account.last_error), 500)


@tagged("post_install", "-at_install", "era_ai_accounts")
class TestEmbeddingRouting(TransactionCase):
    """Knowledge-source embeddings must reach a provider that can serve them."""

    def test_custom_llm_embedding_model_is_writable(self):
        """The option we advertise must survive Selection validation.

        ``fields.Selection`` freezes its list into ``_selection`` when the class
        body of ``ai`` runs, i.e. before we append our provider — so without the
        ``ai.embedding._register_hook`` re-sync the value renders in the UI but
        every write raises "Wrong value for ai.embedding.embedding_model", and
        knowledge sources sit in ``processing`` forever with zero embeddings.
        """
        from odoo.addons.era_ai_accounts.models.llm_providers_patch import (
            CUSTOM_LLM_EMBEDDING_KEY,
        )
        field = self.env["ai.embedding"]._fields["embedding_model"]
        self.assertIn(CUSTOM_LLM_EMBEDDING_KEY, dict(field.selection),
                      "provider patch must expose the option")
        self.assertIn(CUSTOM_LLM_EMBEDDING_KEY, field._selection,
                      "the frozen validation copy must expose it too")
        # The real check: an actual assignment goes through convert_to_cache.
        record = self.env["ai.embedding"].new({
            "embedding_model": CUSTOM_LLM_EMBEDDING_KEY,
        })
        self.assertEqual(record.embedding_model, CUSTOM_LLM_EMBEDDING_KEY)

    def test_cli_proxy_agent_falls_back_to_an_embeddable_model(self):
        """A CLI-proxy account is text-only, so RAG must not be routed to it."""
        # This database may already point at a local embeddings service; the
        # test has to define its own starting point rather than inherit one.
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ai.embedding_model_override", "")
        params.set_param("ai.embedding_fallback_model", "")
        agent = self.env["ai.agent"].create({
            "name": "RAG agent", "llm_model": "custom_llm/custom",
        })
        self.assertEqual(agent._get_embedding_model(),
                         "custom_llm/text-embedding-3-small",
                         "with no account the standard derivation applies")

        agent.era_account_id = self.env['era.ai.account'].create({
            "name": "Codex", "provider": "openai", "auth_mode": "cli_proxy",
        })
        self.assertEqual(agent._get_embedding_model(), "text-embedding-3-small",
                         "CLI proxies cannot embed — fall back to a provider that can")

        params.set_param("ai.embedding_fallback_model", "gemini-embedding-2")
        self.assertEqual(agent._get_embedding_model(), "gemini-embedding-2",
                         "the fallback provider must be configurable")

        params.set_param("ai.embedding_model_override", "text-embedding-3-small")
        agent.era_account_id = False
        self.assertEqual(agent._get_embedding_model(), "text-embedding-3-small",
                         "an explicit override wins even without an account")

    def test_arabic_text_document_yields_content(self):
        """Core's ASCII-only indexer must not silence a non-Latin document."""
        arabic = (
            "نظام المحاماة الصادر بالمرسوم الملكي رقم م/38 وتاريخ 28/7/1422هـ\n"
            "المادة الأولى: يقصد بمهنة المحاماة الترافع عن الغير أمام المحاكم.\n"
        )
        attachment = self.env["ir.attachment"].create({
            "name": "نظام المحاماة.txt",
            "raw": arabic.encode("utf-8"),
            "mimetype": "text/plain",
        })
        self.assertNotIn("المحاماة", attachment.index_content or "",
                         "core keeps only ASCII — that is the bug we work around")
        content = attachment._get_attachment_content() or ""
        self.assertIn("المحاماة", content)
        self.assertIn("الترافع عن الغير أمام المحاكم", content,
                      "the whole document must survive, not fragments of it")

    def test_latin_text_still_uses_the_core_index(self):
        attachment = self.env["ir.attachment"].create({
            "name": "policy.txt",
            "raw": b"Retainer agreements are reviewed by the managing partner.",
            "mimetype": "text/plain",
        })
        self.assertIn("Retainer", attachment._get_attachment_content() or "")

    def test_binary_attachment_is_left_alone(self):
        """A non-text mimetype must keep returning whatever core decided."""
        attachment = self.env["ir.attachment"].create({
            "name": "scan.png", "raw": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
            "mimetype": "image/png",
        })
        self.assertFalse(attachment._get_attachment_content())

    # ------------------------------------------------------- E5 prefixes
    def test_e5_models_get_the_query_passage_prefix(self):
        """E5 is trained with these prefixes and scores badly without them."""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ai.custom_llm_embedding_model", "intfloat/multilingual-e5-large")
        svc = LLMApiService(self.env, provider="custom_llm")
        self.assertEqual(
            svc._format_for_embedding(content="نظام المحاماة", mode="document", title="t"),
            "passage: نظام المحاماة")
        self.assertEqual(
            svc._format_for_embedding(content="ما شروط القيد؟", mode="query"),
            "query: ما شروط القيد؟")

    def test_non_e5_custom_endpoints_get_raw_text(self):
        """OpenRouter and friends must not be fed an E5 prefix."""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ai.custom_llm_embedding_model", "openai/text-embedding-3-small")
        svc = LLMApiService(self.env, provider="custom_llm")
        self.assertEqual(
            svc._format_for_embedding(content="نظام المحاماة", mode="document", title="t"),
            "نظام المحاماة")

    def test_other_providers_keep_core_behaviour(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ai.custom_llm_embedding_model", "intfloat/multilingual-e5-large")
        svc = LLMApiService(self.env, provider="openai")
        self.assertEqual(
            svc._format_for_embedding(content="contract review", mode="document", title="t"),
            "contract review", "the E5 prefix must not leak onto OpenAI")

    def test_embeddings_can_live_on_a_different_endpoint_than_chat(self):
        """A local embeddings service must not swallow the chat endpoint.

        The local service answers /v1/embeddings only and 404s on chat, so
        pointing the shared ai.custom_llm_base_url at it would break every
        agent that has no account to fall back on.
        """
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ai.custom_llm_base_url", "https://openrouter.ai/api/v1")
        params.set_param("ai.custom_llm_embedding_base_url", "http://127.0.0.1:8091/v1")
        params.set_param("ai.custom_llm_key", "k")
        svc = LLMApiService(self.env, provider="custom_llm")
        self.assertEqual(svc.base_url, "https://openrouter.ai/api/v1",
                         "chat keeps the configured chat endpoint")

        seen = []

        def fake_request(self_, method, endpoint, headers, body, **kw):
            seen.append((endpoint, self_.base_url))
            return {"data": [{"embedding": [0.0]}]}

        with patch.object(type(svc), "_request", fake_request):
            svc.get_embedding(input=["x"], dimensions=1536)
        self.assertEqual(seen, [("/embeddings", "http://127.0.0.1:8091/v1")],
                         "the embedding call goes to the embedding endpoint")
        self.assertEqual(svc.base_url, "https://openrouter.ai/api/v1",
                         "and the chat endpoint is restored afterwards")

    def test_one_endpoint_serves_both_when_nothing_special_is_set(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ai.custom_llm_base_url", "https://openrouter.ai/api/v1")
        params.set_param("ai.custom_llm_embedding_base_url", "")
        params.set_param("ai.custom_llm_key", "k")
        svc = LLMApiService(self.env, provider="custom_llm")
        seen = []

        def fake_request(self_, method, endpoint, headers, body, **kw):
            seen.append(self_.base_url)
            return {"data": [{"embedding": [0.0]}]}

        with patch.object(type(svc), "_request", fake_request):
            svc.get_embedding(input=["x"], dimensions=1536)
        self.assertEqual(seen, ["https://openrouter.ai/api/v1"])

    def _hundred_chunks(self):
        """Real records: core sizes its batches from the chunk contents."""
        attachment = self.env["ir.attachment"].create({
            "name": "نظام.txt", "raw": b"x" * 64, "mimetype": "text/plain",
        })
        return self.env["ai.embedding"].create([{
            "attachment_id": attachment.id,
            "content": f"passage: مادة رقم {i} من النظام",
            "embedding_model": "custom_llm/text-embedding-3-small",
        } for i in range(100)])

    def test_custom_llm_batches_are_capped_for_slow_endpoints(self):
        """A CPU embedder cannot finish 2048 chunks before the socket times out."""
        self.env["ir.config_parameter"].sudo().set_param(
            "ai.custom_llm_embedding_batch_size", "32")
        chunks = self._hundred_chunks()
        batches = self.env["ai.embedding"]._create_batches(chunks, "custom_llm")
        self.assertTrue(batches, "there must be batches to send")
        self.assertTrue(all(len(b) <= 32 for b in batches),
                        f"no batch may exceed the cap, got {[len(b) for b in batches]}")
        self.assertEqual(sum(len(b) for b in batches), 100, "nothing may be dropped")

    def test_an_unset_cap_leaves_batching_alone(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "ai.custom_llm_embedding_batch_size", "")
        chunks = self._hundred_chunks()
        batches = self.env["ai.embedding"]._create_batches(chunks, "custom_llm")
        self.assertEqual(sum(len(b) for b in batches), 100)
        self.assertTrue(any(len(b) > 32 for b in batches),
                        "without a cap the provider's own batching applies")

    def test_other_providers_are_not_capped(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "ai.custom_llm_embedding_batch_size", "32")
        chunks = self._hundred_chunks()
        batches = self.env["ai.embedding"]._create_batches(chunks, "openai")
        self.assertEqual(sum(len(b) for b in batches), 100)
        self.assertTrue(any(len(b) > 32 for b in batches),
                        "the cap is for the slow custom endpoint only")
