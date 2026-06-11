import base64
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import time
from urllib.parse import urlencode, urlparse

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import config

from odoo.addons.ai.utils.llm_api_service import LLMApiService

from ..utils import codex_cli_transport
from ..utils import crypto
from ..utils import llm_cli_transport

_logger = logging.getLogger(__name__)

MANAGER_GROUP = "era_ai_accounts.group_ai_account_manager"

# --- Claude Code OAuth (subscription "Login with Claude") --------------------
# Public Claude Code OAuth client parameters used by the `claude /login` /
# `setup-token` flow. The server has no browser, so we use the manual
# "copy code" redirect: an admin authorises in their own browser and pastes the
# returned ``code#state`` string back into Odoo. We mint the token and write it
# to the standard ``.credentials.json`` that the first-party ``claude`` binary
# reads — we never replay the token to api.anthropic.com ourselves (the CLI does
# the call under its own auth). Unlike Claudoo's per-user login, this links ONE
# account, stored under the Odoo data dir and used by every user in the system.
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
# Transient PKCE state (verifier + state) stashed per account while a login is in
# flight. Cleared once the code is exchanged.
_PKCE_PARAM = "era_ai_accounts.oauth_pkce.%s"

# Providers that have a first-party local CLI we can proxy through (the
# "connected account" path): Anthropic's `claude` and OpenAI's `codex`.
CLI_PROXY_PROVIDERS = ("anthropic", "openai")

# Per-provider layout of the linked-account credential store, mirroring what
# each first-party CLI expects:
#  - claude reads `$CLAUDE_CONFIG_DIR/.credentials.json`;
#  - codex reads `$CODEX_HOME/auth.json` (CODEX_HOME *is* the config dir).
# The matching env var is exported by each transport, not stored here.
_CLI_PROFILES = {
    "anthropic": {"subdir": ".claude", "cred_file": ".credentials.json"},
    "openai": {"subdir": ".codex", "cred_file": "auth.json"},
}

# Curated chat models for the Claude CLI-proxy (the CLI has no list endpoint).
# Use the CLI's *aliases*: `claude --model` resolves them to the latest model of
# each tier the connected account supports, so they never go stale across version
# bumps. Full ids (e.g. claude-opus-4-8) also work if you prefer pinning.
CLAUDE_CLI_MODELS = [
    ("opus", "Claude Opus (latest)"),
    ("sonnet", "Claude Sonnet (latest)"),
    ("haiku", "Claude Haiku (latest)"),
]

# Curated chat models for the Codex CLI-proxy (ChatGPT-plan auth has no model
# list endpoint either). Unlike Claude there are no version-proof aliases, so
# these are concrete slugs (valid for codex-cli 0.139 / June 2026) — re-verify
# with `codex exec -m <slug>` after CLI upgrades; the catalog rows are editable
# per account if a plan serves different models.
CODEX_CLI_MODELS = [
    ("gpt-5.3-codex", "GPT-5.3 Codex"),
    ("gpt-5.4", "GPT-5.4"),
    ("gpt-5.4-mini", "GPT-5.4 Mini"),
]

# Curated Cloudflare Workers AI models — (model_id, label, kind, rate).
# Cloudflare bills in "Neurons" with 10,000 free per day; it does NOT expose
# per-model price via its API, so the rates below are captured from the public
# pricing page (as of 2026-06) and shown as guidance only — verify the live page:
# https://developers.cloudflare.com/workers-ai/platform/pricing/
CLOUDFLARE_FREE_NEURONS_PER_DAY = 10000
CLOUDFLARE_MODELS = [
    ("@cf/meta/llama-3.1-8b-instruct", "Llama 3.1 8B Instruct", "chat",
     "≈25,608 in / 75,147 out neurons per 1M tokens"),
    ("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "Llama 3.3 70B (fast)", "chat",
     "≈26,668 in / 204,805 out neurons per 1M tokens"),
    # Image models (text-to-image). FLUX.2 gives much better article-hero quality
    # than FLUX.1 schnell; schnell stays as the commercial-safe (Apache-2.0) default.
    ("@cf/black-forest-labs/flux-1-schnell", "FLUX.1 [schnell] — fast, Apache-2.0", "image",
     "≈9.60 neurons/step (4.80 per 512×512 tile)"),
    ("@cf/black-forest-labs/flux-2-dev", "FLUX.2 [dev] — highest quality", "image",
     "FLUX.2 — billed in Neurons; see pricing page"),
    ("@cf/black-forest-labs/flux-2-klein-9b", "FLUX.2 [klein] 9B — fast, high quality", "image",
     "FLUX.2 — billed in Neurons; see pricing page"),
    ("@cf/black-forest-labs/flux-2-klein-4b", "FLUX.2 [klein] 4B — fastest", "image",
     "FLUX.2 — billed in Neurons; see pricing page"),
    ("@cf/bytedance/stable-diffusion-xl-lightning", "SDXL Lightning — fast", "image",
     "billed in Neurons; see pricing page"),
    ("@cf/baai/bge-m3", "BGE-M3 (embeddings)", "embedding",
     "≈1,075 neurons per 1M input tokens"),
]
CLOUDFLARE_DEFAULT_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

# Curated OpenAI image models (the /models endpoint lists chat models only, not
# image ones). Much higher quality than free Cloudflare FLUX, at a per-image cost.
OPENAI_IMAGE_MODELS = [
    ("gpt-image-1", "GPT Image 1 (high quality)", "image", "paid — see OpenAI image pricing"),
    ("dall-e-3", "DALL·E 3", "image", "paid — see OpenAI image pricing"),
]


def _codex_plan_from_id_token(id_token):
    """Best-effort ChatGPT plan name ('plus', 'pro', …) from a Codex id_token.

    The id_token is a JWT whose payload carries the plan under OpenAI's auth
    claim. Decoded without signature verification — it is only used for a
    display label, never for authorization decisions.
    """
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (IndexError, ValueError, AttributeError):
        return ""
    if not isinstance(claims, dict):
        return ""
    auth_claim = claims.get("https://api.openai.com/auth")
    plan = auth_claim.get("chatgpt_plan_type") if isinstance(auth_claim, dict) else ""
    return str(plan or claims.get("chatgpt_plan_type") or "")


class EraAiAccount(models.Model):
    _name = "era.ai.account"
    _description = "AI Provider Account"
    _order = "scope, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    provider = fields.Selection(
        selection=[
            ("anthropic", "Anthropic (Claude)"),
            ("openai", "OpenAI"),
            ("google", "Google Gemini"),
            ("cloudflare", "Cloudflare Workers AI"),
            ("custom", "Custom (OpenAI-compatible)"),
        ],
        required=True,
        default="anthropic",
    )
    auth_mode = fields.Selection(
        selection=[
            ("cli_proxy", "Local CLI proxy (connected account)"),
            ("api_key", "API key"),
        ],
        required=True,
        default="cli_proxy",
        help="CLI proxy routes calls through a locally-authenticated first-party "
             "CLI — Claude Code for Anthropic, Codex for OpenAI — using the "
             "connected account (no API key, no per-token billing). API key calls "
             "the provider over HTTP.",
    )

    # --- Sharing / ownership -------------------------------------------------
    scope = fields.Selection(
        selection=[("shared", "Shared"), ("personal", "Personal")],
        required=True,
        default="shared",
        help="Shared: usable by every allowed user in the company. "
             "Personal: usable only by the owner and explicitly allowed users.",
    )
    owner_user_id = fields.Many2one(
        "res.users", string="Owner", index=True,
        default=lambda self: self.env.user, ondelete="cascade",
    )
    allowed_user_ids = fields.Many2many(
        "res.users", string="Allowed Users",
        help="Users who may use this account. For shared accounts, leave empty to "
             "allow everyone in the company.",
    )
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True,
    )

    # --- Guards --------------------------------------------------------------
    kill_switch = fields.Boolean(
        string="Disabled (kill switch)",
        help="Temporarily block all AI calls through this account without archiving it.",
    )
    max_concurrency = fields.Integer(
        default=1,
        help="Legacy hint. CLI-proxy calls are now globally serialized (one at a "
             "time across all workers/users), so concurrency is effectively 1.",
    )

    # --- Secret (encrypted, manager-only) ------------------------------------
    secret_encrypted = fields.Char(
        string="Encrypted secret", copy=False, groups=MANAGER_GROUP,
    )
    secret = fields.Char(
        string="API Key", store=False, groups=MANAGER_GROUP,
        compute="_compute_secret", inverse="_inverse_secret",
        help="Provider API key. Stored encrypted; only AI Account Managers can set it.",
    )
    secret_is_set = fields.Boolean(
        string="Secret stored", compute="_compute_secret_is_set", store=True,
        compute_sudo=True, groups=MANAGER_GROUP,
    )
    secret_masked = fields.Char(
        string="Stored key", compute="_compute_secret_masked", compute_sudo=True,
        groups=MANAGER_GROUP,
    )

    # --- API-key transport config -------------------------------------------
    base_url = fields.Char(help="Base URL for an OpenAI-compatible endpoint (custom provider).")
    auth_header = fields.Char(default="Authorization")
    auth_prefix = fields.Char(default="Bearer")
    referer = fields.Char(help="Optional HTTP-Referer header (custom/OpenRouter).")
    title = fields.Char(help="Optional X-Title header (custom/OpenRouter).")

    # --- Cloudflare Workers AI -----------------------------------------------
    cf_account_id = fields.Char(
        string="Cloudflare Account ID",
        help="Your Cloudflare account ID — it goes in the Workers AI URL path "
             "(api.cloudflare.com/.../accounts/<ID>/ai/...). Find it in the Cloudflare "
             "dashboard. The API token goes in the 'API Key' field (Bearer auth).",
    )

    # --- CLI-proxy transport config -----------------------------------------
    cli_path = fields.Char(
        string="CLI binary path",
        help="Optional override for the AI CLI binary. Auto-detected when empty: "
             "the Claude Code extension binary for Anthropic, `codex` on PATH "
             "(or ERA_AI_CODEX_BIN) for OpenAI.",
    )
    cli_home_dir = fields.Char(
        string="CLI HOME",
        default="/opt/odoo",
        help="HOME directory whose connected-account auth the CLI should use "
             "(~/.claude for Claude, ~/.codex for Codex). Ignored once you link "
             "an account below (the linked credentials take over).",
    )
    cli_extra_args = fields.Char(
        string="CLI extra arguments", groups=MANAGER_GROUP,
        help="Appended verbatim to the CLI invocation (shlex-split). Manager-only: "
             "these flags shape what the subprocess may do.",
    )

    # --- Linked subscription account (Claude or ChatGPT, system-wide) --------
    cli_oauth_linked = fields.Boolean(
        string="Subscription account linked",
        compute="_compute_cli_oauth_linked",
        help="Whether a Claude or ChatGPT subscription has been linked to this "
             "account. The credentials are stored once, on the server, and used "
             "by every user — there is no per-user login.",
    )
    cli_oauth_label = fields.Char(
        string="Linked account", compute="_compute_cli_oauth_linked",
    )

    # --- Models catalog ------------------------------------------------------
    model_ids = fields.One2many("era.ai.model", "account_id", string="Models")
    chat_model_count = fields.Integer(compute="_compute_model_counts")

    # --- Status --------------------------------------------------------------
    state = fields.Selection(
        selection=[("draft", "Not validated"), ("valid", "Validated"), ("error", "Error")],
        default="draft", readonly=True, copy=False,
    )
    last_validated = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)

    # --- Admin note ----------------------------------------------------------
    note = fields.Text(
        string="Note",
        help="Free-form note for administrators (what this account is for, which "
             "key to link, etc.). Never sent to any provider.",
    )

    # ------------------------------------------------------------------ compute
    @api.depends("secret_encrypted")
    def _compute_secret(self):
        # Never echo the stored plaintext back into the form.
        for rec in self:
            rec.secret = False

    def _inverse_secret(self):
        for rec in self:
            if rec.secret:
                rec.secret_encrypted = crypto.encrypt_secret(rec.env, rec.secret)

    @api.depends("secret_encrypted")
    def _compute_secret_is_set(self):
        for rec in self:
            rec.secret_is_set = bool(rec.sudo().secret_encrypted)

    @api.depends("secret_encrypted")
    def _compute_secret_masked(self):
        for rec in self:
            rec.secret_masked = "••••••••" if rec.sudo().secret_encrypted else ""

    @api.depends("model_ids", "model_ids.kind", "model_ids.active")
    def _compute_model_counts(self):
        for rec in self:
            rec.chat_model_count = len(rec.model_ids.filtered(lambda m: m.kind == "chat" and m.active))

    @api.depends()
    def _compute_cli_oauth_linked(self):
        # Reflects on-disk credential state (not an ORM field), so it carries no
        # field-level @api.depends triggers; the explicit empty @api.depends()
        # documents that and silences the framework's "missing depends" warning.
        # Recomputed on each fresh read (e.g. a form reload after linking).
        for rec in self:
            # Only cli_proxy accounts can be linked — skip the disk read otherwise.
            if rec.id and rec.auth_mode == "cli_proxy":
                info = rec.sudo()._cli_oauth_info()
            else:
                info = {}
            rec.cli_oauth_linked = bool(info.get("linked"))
            if not info.get("linked"):
                rec.cli_oauth_label = ""
            elif rec.provider == "openai":
                plan = info.get("subscription") or ""
                rec.cli_oauth_label = (
                    _("ChatGPT %s linked", plan) if plan else _("ChatGPT account linked"))
            else:
                sub = info.get("subscription") or "subscription"
                rec.cli_oauth_label = _("Claude %s linked", sub)

    # ----------------------------------------------------------------- onchange
    @api.onchange("provider")
    def _onchange_provider(self):
        # Only Anthropic (claude) and OpenAI (codex) have a local CLI proxy;
        # everything else needs an API key.
        if self.provider not in CLI_PROXY_PROVIDERS and self.auth_mode == "cli_proxy":
            self.auth_mode = "api_key"

    # ---------------------------------------------------------------- constraints
    @api.constrains("auth_mode", "provider")
    def _check_auth_mode(self):
        for rec in self:
            if rec.auth_mode == "cli_proxy" and rec.provider not in CLI_PROXY_PROVIDERS:
                raise ValidationError(_(
                    "The local CLI proxy is only available for the Anthropic "
                    "(Claude CLI) and OpenAI (Codex CLI) providers."
                ))

    @api.constrains("base_url")
    def _check_base_url(self):
        # Keys/tokens are attached to whatever this URL is — restrict it to real
        # http(s) endpoints so a typo (or worse, file:///...) can't ship them off.
        for rec in self:
            if not rec.base_url:
                continue
            parsed = urlparse(rec.base_url.strip())
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValidationError(_(
                    "The base URL must be an absolute http(s) URL, e.g. "
                    "https://openrouter.ai/api/v1 (got '%s').", rec.base_url))

    # ------------------------------------------------------------------- secrets
    def _get_secret(self):
        self.ensure_one()
        return crypto.decrypt_secret(self.env, self.sudo().secret_encrypted)

    # --------------------------------------------------------------- transport cfg
    def _service_provider(self):
        """Provider token used to build a LLMApiService for this account."""
        self.ensure_one()
        if self.auth_mode == "cli_proxy":
            return "openai_cli" if self.provider == "openai" else "anthropic_cli"
        if self.provider == "custom":
            return "custom_llm"
        return self.provider  # openai / google / anthropic / cloudflare

    _PROVIDER_DEFAULT_MODEL = {
        "anthropic": "claude-opus-4-8",
        "openai": "gpt-4o",
        "google": "gemini-2.5-flash",
        "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
    }

    # ------------------------------------------------------------- Cloudflare
    def _cloudflare_account(self):
        self.ensure_one()
        acct = (self.cf_account_id or "").strip()
        if not acct:
            raise UserError(_("Set the Cloudflare Account ID on account '%s'.", self.name))
        return acct

    def _cloudflare_base_url(self):
        """OpenAI-compatible base for Cloudflare Workers AI (chat/embeddings)."""
        return "https://api.cloudflare.com/client/v4/accounts/%s/ai/v1" % self._cloudflare_account()

    def _cloudflare_run_url(self, model):
        """Native Workers AI run endpoint for a specific model (used for images)."""
        return "https://api.cloudflare.com/client/v4/accounts/%s/ai/run/%s" % (
            self._cloudflare_account(), model)

    def _default_image_model(self):
        """Best image model id for this account (first active image model, else FLUX schnell)."""
        self.ensure_one()
        img = self.model_ids.filtered(lambda m: m.kind == "image" and m.active)
        return img[0].model_id if img else CLOUDFLARE_DEFAULT_IMAGE_MODEL

    @staticmethod
    def _cf_default_steps(model):
        """Sensible inference steps per Cloudflare image model family.

        FLUX.1-schnell / SDXL-Lightning are distilled (few steps); FLUX.2 [dev]
        is the full model and needs many more steps for good quality, while the
        FLUX.2 [klein] distilled variants sit in between.
        """
        m = model or ""
        if "flux-2-dev" in m:
            return 28
        if "flux-2-klein" in m:
            return 8
        return 4  # flux-1-schnell, sdxl-lightning

    def generate_image(self, prompt, model=None, steps=None, width=1024, height=1024):
        """Generate an image and return the raw bytes (PNG/JPEG), or raise.

        Supported providers: Cloudflare Workers AI (FLUX/SDXL) and OpenAI
        (gpt-image-1 / DALL·E). OpenAI gives much higher quality than free
        Cloudflare FLUX, at a per-image cost.
        """
        self.ensure_one()
        self._assert_usable()
        prompt = (prompt or "")[:4000]
        if self.provider == "cloudflare":
            return self._generate_image_cloudflare(prompt, model, steps, width, height)
        if self.provider == "openai":
            if self.auth_mode == "cli_proxy":
                raise UserError(_(
                    "Image generation is not available through the Codex CLI proxy "
                    "(account '%s') — use an OpenAI API-key account for images.",
                    self.name))
            return self._generate_image_openai(prompt, model, width, height)
        raise UserError(_(
            "Image generation is supported for Cloudflare Workers AI and OpenAI "
            "accounts (account '%s' is '%s').", self.name, self.provider))

    def _generate_image_cloudflare(self, prompt, model, steps, width, height):
        """Cloudflare Workers AI image gen. Two request shapes that are NOT
        interchangeable: FLUX.1-schnell / SDXL take JSON ``{"prompt","steps"}``,
        while FLUX.2 REQUIRES ``multipart/form-data`` (prompt/steps/width/height)
        — sending JSON to FLUX.2 fails with 'required properties … multipart'."""
        token = self._get_secret()
        if not token:
            raise UserError(_("Set the Cloudflare API token on account '%s'.", self.name))
        model = model or self._default_image_model()
        if steps is None:
            steps = self._cf_default_steps(model)
        try:
            steps = max(1, min(50, int(steps)))
        except (TypeError, ValueError):
            steps = self._cf_default_steps(model)
        url = self._cloudflare_run_url(model)
        auth = {"Authorization": "Bearer %s" % token}
        try:
            if "flux-2" in model:
                # FLUX.2 unified input: multipart/form-data. Let requests set the
                # Content-Type (with boundary) by passing files=.
                resp = requests.post(
                    url, headers=auth, timeout=120,
                    files={
                        "prompt": (None, prompt[:2048]),
                        "steps": (None, str(steps)),
                        "width": (None, str(int(width))),
                        "height": (None, str(int(height))),
                    },
                )
            else:
                resp = requests.post(
                    url, headers=dict(auth, **{"Content-Type": "application/json"}),
                    json={"prompt": prompt[:2048], "steps": steps}, timeout=120,
                )
        except requests.exceptions.RequestException as exc:
            raise UserError(_("Cloudflare image request failed: %s", exc))
        if resp.status_code >= 400:
            raise UserError(_("Cloudflare image error (%(code)s): %(detail)s",
                              code=resp.status_code, detail=(resp.text or "")[:400]))
        if "application/json" in (resp.headers.get("Content-Type") or ""):
            data = resp.json()
            if not data.get("success", True):
                raise UserError(_("Cloudflare image error: %s", data.get("errors") or "unknown"))
            b64 = (data.get("result") or {}).get("image") or data.get("image")
            if not b64:
                raise UserError(_("Cloudflare returned no image data."))
            return base64.b64decode(b64)
        return resp.content

    def _generate_image_openai(self, prompt, model, width, height):
        """OpenAI image generation (gpt-image-1 / DALL·E 3). High quality, paid."""
        key = self._get_secret()
        if not key:
            raise UserError(_("Set the OpenAI API key on account '%s'.", self.name))
        model = model or "gpt-image-1"
        body = {"model": model, "prompt": prompt[:4000], "n": 1,
                "size": "%dx%d" % (int(width), int(height))}
        if model.startswith("gpt-image"):
            body["quality"] = "high"  # article heroes — favor quality
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        # gpt-image at high quality routinely takes 1-3 minutes; the timeout is
        # generous (and ICP-tunable) so a slow-but-successful render isn't cut off.
        try:
            timeout = int(self.env["ir.config_parameter"].sudo().get_param(
                "ai.openai_image_timeout", "300"))
        except (TypeError, ValueError):
            timeout = 300
        try:
            resp = requests.post(
                base + "/images/generations",
                headers={"Authorization": "Bearer %s" % key, "Content-Type": "application/json"},
                json=body, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            raise UserError(_("OpenAI image request failed: %s", exc))
        if resp.status_code >= 400:
            raise UserError(_("OpenAI image error (%(code)s): %(detail)s",
                              code=resp.status_code, detail=(resp.text or "")[:400]))
        try:
            item = resp.json()["data"][0]
        except (ValueError, KeyError, IndexError) as exc:
            raise UserError(_("OpenAI returned an unexpected image response: %s", exc))
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            try:
                # (connect, read) tuple: fail fast on a stalled connection while
                # still tolerating a slow CDN transfer.
                dl = requests.get(item["url"], timeout=(5, 60))
                dl.raise_for_status()
                return dl.content
            except requests.exceptions.RequestException as exc:
                raise UserError(_("OpenAI image download failed: %s", exc))
        raise UserError(_("OpenAI returned no image."))

    # --------------------------------------------------------------- public API
    def generate_text(self, prompt, system="", model=None, temperature=0.2):
        """Generate a text/chat completion through this account; return the string.

        Provider-agnostic: works for the Claude CLI proxy, OpenAI/Google/Anthropic
        keys, Cloudflare Workers AI, and custom OpenAI-compatible endpoints. Lets
        any module use an account for content without wiring up an ai.agent.
        """
        self.ensure_one()
        self._assert_usable()
        model = model or self._default_chat_model()
        system_messages = [system] if system else []
        service = LLMApiService(
            env=self.with_context(era_ai_account_id=self.id).env,
            provider=self._service_provider(),
        )
        response = service.request_llm(
            model, system_messages, [],
            inputs=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        if isinstance(response, (list, tuple)):
            response = response[0] if response else ""
        return response if isinstance(response, str) else (str(response) if response else "")

    def _preferred_chat_model(self):
        """Preferred default model id for this account, by provider + auth mode.

        The Claude CLI proxy uses the version-proof alias 'opus'; the Codex CLI
        has no aliases, so its default is a concrete slug. HTTP-API accounts use
        the provider defaults (e.g. the ChatGPT plan's gpt-5.x slugs are not
        valid API-key models and vice versa)."""
        self.ensure_one()
        if self.auth_mode == "cli_proxy":
            if self.provider == "anthropic":
                return "opus"
            if self.provider == "openai":
                return CODEX_CLI_MODELS[0][0]
        return self._PROVIDER_DEFAULT_MODEL.get(self.provider)

    def _default_chat_model(self):
        """Best chat model id for this account: the provider's preferred model if it is
        in the catalog, else the first catalogued chat model, else a sane default."""
        self.ensure_one()
        chat = self.model_ids.filtered(lambda m: m.kind == "chat" and m.active)
        default = self._preferred_chat_model()
        if default and chat.filtered(lambda m: m.model_id == default):
            return default
        if chat:
            return chat[0].model_id
        if default:
            return default
        raise UserError(_("No chat model configured for account '%s'. Sync models first.", self.name))

    def _default_chat_model_record(self):
        """Return the era.ai.model record matching _default_chat_model (or empty)."""
        self.ensure_one()
        try:
            target = self._default_chat_model()
        except UserError:
            return self.env["era.ai.model"]
        return self.model_ids.filtered(
            lambda m: m.kind == "chat" and m.active and m.model_id == target)[:1]

    # -------------------------------------------- linked-account config dir
    def _cli_profile(self):
        """Credential layout of this account's first-party CLI (see _CLI_PROFILES)."""
        self.ensure_one()
        return _CLI_PROFILES.get(self.provider) or _CLI_PROFILES["anthropic"]

    def _cli_managed_home(self):
        """HOME for a linked account — isolated, under the Odoo data dir.

        Deliberately NOT the server's own HOME (/opt/odoo): linking here must
        never overwrite the server operator's own `~/.claude` / `~/.codex`
        login. One directory per account record; in practice the business links
        a single shared account that every user routes through.
        """
        self.ensure_one()
        data_dir = config.get("data_dir") or "/var/lib/odoo"
        return os.path.join(data_dir, "era_ai_accounts", "cli", str(self.id or "new"))

    def _cli_managed_config_dir(self, create=False):
        """The linked account's private CLI config dir, in each CLI's native
        layout: `<home>/.claude` (CLAUDE_CONFIG_DIR, holds .credentials.json)
        for Claude, `<home>/.codex` (CODEX_HOME, holds auth.json) for Codex."""
        self.ensure_one()
        home = self._cli_managed_home()
        path = os.path.join(home, self._cli_profile()["subdir"])
        if create:
            try:
                os.makedirs(path, mode=0o700, exist_ok=True)
                # makedirs masks mode by umask and only the leaf is guaranteed;
                # force the account-private dirs to 0700 so a linked account's
                # presence/ids aren't world-listable (the token file is 0600).
                for p in (home, path):
                    try:
                        os.chmod(p, 0o700)
                    except OSError as exc:
                        _logger.warning(
                            "era_ai_accounts: cannot restrict permissions on %s: %s", p, exc)
            except OSError as e:
                raise UserError(_("Cannot create the CLI config dir %s: %s", path, e))
        return path

    def _cli_credentials_path(self):
        self.ensure_one()
        return os.path.join(self._cli_managed_config_dir(), self._cli_profile()["cred_file"])

    def _cli_oauth_info(self):
        """Read the linked credentials; return {linked, subscription, expires_at}."""
        self.ensure_one()
        try:
            with open(self._cli_credentials_path(), "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"linked": False}
        if not isinstance(data, dict):
            return {"linked": False}
        if self.provider == "openai":
            return self._codex_oauth_info(data)
        oauth = data.get("claudeAiOauth") or {}
        if not oauth.get("accessToken"):
            return {"linked": False}
        return {
            "linked": True,
            "subscription": oauth.get("subscriptionType") or "",
            "expires_at": oauth.get("expiresAt") or 0,
        }

    @staticmethod
    def _codex_oauth_info(data):
        """Interpret a Codex ``auth.json``: ChatGPT-mode tokens => linked."""
        tokens = data.get("tokens") or {}
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            return {"linked": False}
        # auth_mode is "chatgpt" for subscription auth; absent on some older
        # files — only an explicit API-key mode disqualifies.
        if (data.get("auth_mode") or "chatgpt") != "chatgpt":
            return {"linked": False}
        return {
            "linked": True,
            "subscription": _codex_plan_from_id_token(tokens.get("id_token") or ""),
            "expires_at": 0,  # codex tracks freshness itself via last_refresh
        }

    def _cli_is_linked(self):
        self.ensure_one()
        return bool(self.sudo()._cli_oauth_info().get("linked"))

    def _cli_write_credentials(self, creds):
        """Atomically write `.credentials.json` (mode 0600) for the linked account."""
        self.ensure_one()
        self._cli_managed_config_dir(create=True)
        path = self._cli_credentials_path()
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(creds, f)
        os.replace(tmp, path)

    # ----------------------------------------------------- OAuth (PKCE) login
    def _assert_oauth_manager(self):
        """Linking an account is a system-wide credential change: managers only."""
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group(MANAGER_GROUP)):
            raise UserError(_("Only AI Account Managers can link or unlink a subscription account."))

    def _oauth_start(self):
        """Begin a login: mint a PKCE pair, stash it, return the authorize URL."""
        self.ensure_one()
        self._assert_oauth_manager()
        if self.auth_mode != "cli_proxy":
            raise UserError(_("Linking a Claude account only applies to CLI-proxy accounts."))
        if self.provider != "anthropic":
            # OpenAI's OAuth client only redirects to localhost:1455 (no hosted
            # copy-code page), so there is no in-app OAuth for it — ChatGPT
            # accounts are linked by pasting codex's auth.json instead.
            raise UserError(_(
                "In-app OAuth login is only available for Anthropic (Claude). "
                "For OpenAI, run 'codex login' on your own computer and paste "
                "the auth.json contents in the connect dialog."))
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        self.env["ir.config_parameter"].sudo().set_param(
            _PKCE_PARAM % self.id, json.dumps({"verifier": verifier, "state": state}))
        params = {
            "code": "true",
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": OAUTH_SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        return "%s?%s" % (OAUTH_AUTHORIZE_URL, urlencode(params))

    def _oauth_complete(self, pasted_code):
        """Exchange the pasted ``code#state`` for tokens and persist them."""
        self.ensure_one()
        self._assert_oauth_manager()
        pasted_code = (pasted_code or "").strip()
        if not pasted_code:
            raise UserError(_("Paste the authorization code from Claude."))
        ICP = self.env["ir.config_parameter"].sudo()
        raw = ICP.get_param(_PKCE_PARAM % self.id)
        if not raw:
            raise UserError(_("No login in progress. Click “Login with Claude” first."))
        try:
            pkce = json.loads(raw)
        except ValueError:
            ICP.set_param(_PKCE_PARAM % self.id, "")
            raise UserError(_("The login session is corrupted. Please log in again."))
        # The console callback returns "<code>#<state>". Require the state and an
        # exact match (strict OAuth CSRF check) — Claude always includes it, so a
        # missing state means a truncated paste.
        code, _sep, state = pasted_code.partition("#")
        if not state or state != pkce.get("state"):
            raise UserError(_(
                "Authorization state missing or mismatched — paste the complete "
                "code exactly as Claude shows it, or log in again."))
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "state": state,
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "code_verifier": pkce["verifier"],
        }
        try:
            resp = requests.post(
                OAUTH_TOKEN_URL, json=payload,
                headers={"Content-Type": "application/json"}, timeout=30)
        except requests.RequestException as e:
            raise UserError(_("Could not reach Claude to exchange the code: %s", e))
        if resp.status_code != 200:
            # Log only the structured error type/message, not the raw body (it may
            # carry request ids / account metadata we don't need in the log).
            try:
                err = (resp.json() or {}).get("error") or {}
                detail = "%s: %s" % (err.get("type", "unknown"), err.get("message", "unknown"))
            except ValueError:
                detail = (resp.text or "")[:200]
            _logger.warning("Claude OAuth exchange failed (HTTP %s): %s",
                            resp.status_code, detail)
            raise UserError(_(
                "Claude rejected the authorization code (HTTP %s). The code may have "
                "expired — please log in again.", resp.status_code))
        data = resp.json()
        access = data.get("access_token")
        if not access:
            raise UserError(_("Claude did not return an access token."))
        expires_in = int(data.get("expires_in") or 0)
        scopes = (data.get("scope") or OAUTH_SCOPES).split()
        creds = {
            "claudeAiOauth": {
                "accessToken": access,
                "refreshToken": data.get("refresh_token") or "",
                # Claude Code stores the expiry as a millisecond epoch.
                "expiresAt": int((time.time() + expires_in) * 1000),
                "scopes": scopes,
                "subscriptionType": data.get("subscription_type") or "",
            }
        }
        self.sudo()._cli_write_credentials(creds)
        ICP.set_param(_PKCE_PARAM % self.id, "")  # consume the PKCE state
        return True

    def _oauth_logout(self):
        """Remove the linked account's stored credentials (forces re-link)."""
        self.ensure_one()
        self._assert_oauth_manager()
        try:
            os.remove(self.sudo()._cli_credentials_path())
        except OSError:
            pass
        self.env["ir.config_parameter"].sudo().set_param(_PKCE_PARAM % self.id, "")
        return True

    # ------------------------------------------- ChatGPT (Codex auth.json) link
    def _codex_link_with_auth_json(self, payload):
        """Link a ChatGPT account by storing a pasted Codex ``auth.json``.

        This is OpenAI's officially documented pattern for servers/CI: the
        manager runs ``codex login`` on their own machine (browser OAuth) and
        copies ``~/.codex/auth.json`` here. Codex then refreshes and rewrites
        the file itself during use; we never call OpenAI's OAuth endpoints.
        """
        self.ensure_one()
        self._assert_oauth_manager()
        if self.auth_mode != "cli_proxy" or self.provider != "openai":
            raise UserError(_(
                "Pasting a Codex auth.json only applies to OpenAI CLI-proxy accounts."))
        payload = (payload or "").strip()
        if not payload:
            raise UserError(_("Paste the contents of your ~/.codex/auth.json file."))
        try:
            data = json.loads(payload)
        except ValueError:
            raise UserError(_(
                "That is not valid JSON. Paste the complete, unmodified contents "
                "of ~/.codex/auth.json."))
        if not isinstance(data, dict):
            raise UserError(_("Unexpected auth.json structure (expected a JSON object)."))
        tokens = data.get("tokens") or {}
        if (
            not isinstance(tokens, dict)
            or not tokens.get("access_token")
            or not tokens.get("refresh_token")
        ):
            if data.get("OPENAI_API_KEY"):
                raise UserError(_(
                    "This auth.json contains an API key, not a ChatGPT login. "
                    "Sign in with 'codex login' (choose 'Sign in with ChatGPT') "
                    "and paste the resulting auth.json — or use an API-key "
                    "account instead."))
            raise UserError(_(
                "This auth.json has no ChatGPT tokens (tokens.access_token / "
                "refresh_token). Run 'codex login' on your computer first; if "
                "your auth.json looks empty, set cli_auth_credentials_store = "
                "\"file\" in ~/.codex/config.toml and log in again."))
        if (data.get("auth_mode") or "chatgpt") != "chatgpt":
            raise UserError(_(
                "This auth.json is not a ChatGPT-account login (auth_mode=%s).",
                data.get("auth_mode")))
        # Store the file verbatim (normalized JSON): codex needs last_refresh,
        # account_id, etc. intact, and will rotate the tokens in place later.
        self.sudo()._cli_write_credentials(data)
        return True

    def action_ai_claude_login(self):
        """Button: open the link-account wizard (Claude OAuth / ChatGPT auth.json)."""
        self.ensure_one()
        self._assert_oauth_manager()
        if not self.id:
            raise UserError(_("Save the account first, then link a subscription."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect ChatGPT account") if self.provider == "openai"
                    else _("Login with Claude"),
            "res_model": "era.ai.account.login",
            "view_mode": "form",
            "target": "new",
            "context": {"default_account_id": self.id},
        }

    def action_ai_claude_logout(self):
        """Button: disconnect the linked subscription account."""
        for rec in self:
            rec._oauth_logout()
        return self._notify(_("Subscription account disconnected."))

    def unlink(self):
        # Remove any linked-Claude credential dir so deleting an account leaves no
        # orphaned tokens on disk (best-effort; never blocks the delete).
        for rec in self:
            try:
                shutil.rmtree(rec._cli_managed_home(), ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            self.env["ir.config_parameter"].sudo().set_param(_PKCE_PARAM % rec.id, "")
        return super().unlink()

    def _cli_cfg(self):
        self.ensure_one()
        param = self.env["ir.config_parameter"].sudo()

        def _f(key, default):
            try:
                return float(param.get_param(key, default))
            except (TypeError, ValueError):
                return float(default)

        def _b(key, default=True):
            v = param.get_param(key)
            if v in (None, "", False):
                return default
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        def _i(key, default):
            try:
                return int(float(param.get_param(key, default)))
            except (TypeError, ValueError):
                return int(default)

        concurrency = max(1, _i("ai.cli_max_concurrency", 1))

        # A linked subscription account routes through its own isolated
        # credentials dir; otherwise fall back to the ambient HOME-based login.
        if self._cli_is_linked():
            home_dir = self._cli_managed_home()
            # create=True: guarantee the dir exists for CLAUDE_CONFIG_DIR /
            # CODEX_HOME even if it was removed out from under us between the
            # link check and the call.
            config_dir = self._cli_managed_config_dir(create=True)
        else:
            home_dir = self.cli_home_dir or "/opt/odoo"
            config_dir = False

        return {
            "account_id": self.id,
            "provider": self.provider,
            "cli_path": self.cli_path or False,
            "home_dir": home_dir,
            "config_dir": config_dir,
            # sudo: the field is manager-restricted, but any allowed user's AI
            # call must still be able to build the transport config.
            "extra_args": self.sudo().cli_extra_args or False,
            # Throttle (configurable in Settings > AI): up to `concurrency` CLI calls
            # at a time across the whole host, separated by a size-scaled gap.
            "gap_enabled": _b("ai.cli_gap_enabled", True),
            "min_gap": _f("ai.cli_min_gap", "1.0"),
            "gap_per_kb": _f("ai.cli_gap_per_kb", "0.05"),
            "max_gap": _f("ai.cli_max_gap", "30"),
            "concurrency": concurrency,
            "lock_wait": _f("ai.cli_lock_wait", "300"),
        }

    def _assert_usable(self):
        self.ensure_one()
        if self.kill_switch:
            raise UserError(_("AI account '%s' is disabled (kill switch).", self.name))
        if not self.active:
            raise UserError(_("AI account '%s' is archived.", self.name))

    # ---------------------------------------------------------------- resolution
    @api.model
    def _resolve_for_user(self, user=None, provider=None, account_id=None):
        """Pick a usable account for ``user`` (personal first, then shared)."""
        user = user or self.env.user
        if account_id:
            acc = self.browse(account_id).exists()
            if acc:
                return acc
        domain = [("active", "=", True), ("kill_switch", "=", False)]
        if provider:
            domain.append(("provider", "=", provider))
        accounts = self.search(domain)
        usable = accounts.filtered(lambda a: a._user_can_use(user))
        personal = usable.filtered(lambda a: a.scope == "personal" and a.owner_user_id == user)
        if personal:
            return personal[0]
        return usable[0] if usable else self.browse()

    def _user_can_use(self, user):
        self.ensure_one()
        if self.scope == "shared":
            return not self.allowed_user_ids or user in self.allowed_user_ids
        return user == self.owner_user_id or user in self.allowed_user_ids

    # ------------------------------------------------------------------- actions
    def action_validate(self):
        for rec in self:
            try:
                rec._validate_connection()
                rec.write({
                    "state": "valid",
                    "last_validated": fields.Datetime.now(),
                    "last_error": False,
                })
            except Exception as exc:  # noqa: BLE001
                rec.write({"state": "error", "last_error": str(exc)[:2000]})
                raise UserError(_("Validation failed: %s", exc))
        return self._notify(_("Connection validated."))

    def _validate_connection(self):
        self.ensure_one()
        self._assert_usable()
        if self.auth_mode == "cli_proxy":
            if self.provider == "openai":
                # `codex login status` proves both that the binary runs and that
                # the account's credentials (linked or ambient) are accepted.
                return codex_cli_transport.check_login(self._cli_cfg())
            binary = llm_cli_transport.resolve_cli_binary(self.cli_path or None)
            if not binary:
                raise UserError(_("Claude CLI binary not found on this server."))
            # Cheap liveness check: the CLI prints its version without any model call.
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                raise UserError(_("Claude CLI not runnable: %s", (out.stderr or "").strip()))
            return True
        if self.provider == "cloudflare":
            # Verify token + account + Workers AI access without spending tokens.
            token = self._get_secret()
            if not token:
                raise UserError(_("Set the Cloudflare API token before validating."))
            url = ("https://api.cloudflare.com/client/v4/accounts/%s/ai/models/search?per_page=1"
                   % self._cloudflare_account())
            self._http_get_json(url, {"Authorization": "Bearer %s" % token}, 30)
            return True
        # API-key: list models (no token spend) to prove the key works.
        self._http_list_models()
        return True

    def action_sync_models(self):
        for rec in self:
            rec._sync_models()
        return self._notify(_("Models synced."))

    def _sync_models(self):
        self.ensure_one()
        Model = self.env["era.ai.model"]
        # rows: (model_id, label, kind, cost_info)
        if self.auth_mode == "cli_proxy":
            cli_models = CODEX_CLI_MODELS if self.provider == "openai" else CLAUDE_CLI_MODELS
            rows = [(mid, label, "chat", "") for mid, label in cli_models]
        elif self.provider == "cloudflare":
            # Cloudflare has no per-model price API, so use the curated catalog with
            # the published Neuron rates baked in (see CLOUDFLARE_MODELS).
            rows = list(CLOUDFLARE_MODELS)
        elif self.provider == "openai":
            # /models lists chat models only — add the image models so they can be
            # picked for cover generation (gpt-image-1 / DALL·E).
            rows = [(mid, label, kind, "") for mid, label, kind in self._http_list_models()]
            rows += list(OPENAI_IMAGE_MODELS)
        else:
            rows = [(mid, label, kind, "") for mid, label, kind in self._http_list_models()]
        existing = {(m.model_id, m.kind): m for m in self.model_ids}
        seen = set()
        for model_id, label, kind, cost in rows:
            seen.add((model_id, kind))
            vals = {"label": label, "active": True, "cost_info": cost}
            if (model_id, kind) in existing:
                existing[(model_id, kind)].write(vals)
            else:
                Model.create(dict(vals, account_id=self.id, model_id=model_id, kind=kind))
        # Deactivate models that disappeared from the provider (keep history).
        for key, rec in existing.items():
            if key not in seen:
                rec.active = False

    # ---------------------------------------------------------------- HTTP models
    def _http_list_models(self):
        """Return [(model_id, label, kind)] from the provider's models endpoint."""
        self.ensure_one()
        key = self._get_secret()
        if not key:
            raise UserError(_("Set an API key before syncing models."))
        timeout = 30
        if self.provider == "anthropic":
            url = (self.base_url or "https://api.anthropic.com/v1").rstrip("/") + "/models"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            data = self._http_get_json(url, headers, timeout).get("data", [])
            return [(m["id"], m.get("display_name") or m["id"], "chat") for m in data]
        if self.provider == "google":
            url = (self.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/") + "/models"
            data = self._http_get_json(url, {"x-goog-api-key": key}, timeout).get("models", [])
            out = []
            for m in data:
                mid = (m.get("name") or "").split("/")[-1]
                if not mid:
                    continue
                methods = m.get("supportedGenerationMethods") or []
                kind = "embedding" if "embedContent" in methods else "chat"
                if "generateContent" in methods or kind == "embedding":
                    out.append((mid, m.get("displayName") or mid, kind))
            return out
        # openai / custom (OpenAI-compatible)
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        header = self.auth_header or "Authorization"
        prefix = self.auth_prefix or "Bearer"
        headers = {header: f"{prefix} {key}".strip() if prefix else key}
        data = self._http_get_json(base + "/models", headers, timeout).get("data", [])
        return [(m["id"], m["id"], "chat") for m in data]

    def _http_get_json(self, url, headers, timeout):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            detail = ""
            if getattr(exc, "response", None) is not None:
                detail = (exc.response.text or "")[:400]
            raise UserError(_("Provider request failed: %s %s", exc, detail))

    def _notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": message, "type": "success", "sticky": False},
        }
