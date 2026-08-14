import base64
import hashlib
import json
import logging
import os
import secrets
import shlex
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
from ..utils import kimi_cli_transport
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
# Pending codex device-auth login per account: {pid, out, started_at}. Cleared
# once the login completes, fails, or is superseded.
_DEVICE_PARAM = "era_ai_accounts.codex_device.%s"

# Providers that can route through the local CLI proxy. Anthropic's `claude`,
# OpenAI's `codex` and Moonshot's `kimi` use their own first-party binary under
# the connected account's auth; Z.AI (GLM) reuses the SAME `claude` binary but
# pointed at Z.AI's Anthropic-compatible endpoint with a GLM Coding Plan API key
# (see _cli_cfg / llm_cli_transport).
CLI_PROXY_PROVIDERS = ("anthropic", "openai", "zai", "kimi")

# Per-provider layout of the linked-account credential store, mirroring what
# each first-party CLI expects:
#  - claude reads `$CLAUDE_CONFIG_DIR/.credentials.json`;
#  - codex reads `$CODEX_HOME/auth.json` (CODEX_HOME *is* the config dir).
# The matching env var is exported by each transport, not stored here.
#  - kimi reads `$KIMI_CODE_HOME/config.toml` + `SYSTEM.md`, which the transport
#    regenerates per call; its API key travels in the environment, so there is
#    no credential file and no in-app subscription link (cred_file is None).
_CLI_PROFILES = {
    "anthropic": {"subdir": ".claude", "cred_file": ".credentials.json"},
    "openai": {"subdir": ".codex", "cred_file": "auth.json"},
    "kimi": {"subdir": ".kimi-code", "cred_file": None},
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
# list endpoint either). Unlike Claude there are no version-proof aliases, and
# the codex-tuned slugs (gpt-5.x-codex, incl. the unversioned gpt-5-codex) are
# REJECTED when passed explicitly under ChatGPT-account auth ("model is not
# supported when using Codex with a ChatGPT account" — verified live 2026-06 on
# codex-cli 0.139). Only the general gpt-5.x slugs work; re-verify with
# `codex exec -m <slug>` after CLI upgrades — the catalog rows are editable per
# account if a plan serves different models.
CODEX_CLI_MODELS = [
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

# --- Z.AI (Zhipu GLM) --------------------------------------------------------
# Z.AI exposes two compatible surfaces for the SAME GLM models and the SAME API
# key, and this module supports both:
#   * "api_key" mode  -> OpenAI-compatible chat at api.z.ai/api/paas/v4
#                        (/chat/completions, Bearer auth, native tool-calling).
#   * "cli_proxy" mode -> the local `claude` binary pointed at Z.AI's
#                        Anthropic-compatible endpoint (api.z.ai/api/anthropic)
#                        with the key as ANTHROPIC_AUTH_TOKEN — the GLM Coding
#                        Plan flow (flat-rate subscription, no per-token billing).
# Z.AI has no per-model price API, so the rows below carry indicative USD rates
# captured from the public pricing page (2026-06) — verify the live page:
#   https://docs.z.ai/guides/overview/pricing
ZAI_OPENAI_BASE_URL = "https://api.z.ai/api/paas/v4"
ZAI_ANTHROPIC_BASE_URL = "https://api.z.ai/api/anthropic"

# Curated GLM chat models for the OpenAI-compatible API ("api_key" mode), with
# indicative USD rates. Editable per account; "Sync models" re-applies this set.
ZAI_MODELS = [
    # (model_id, label, kind, rate)
    ("glm-4.6", "GLM-4.6 (recommended)", "chat", "≈$0.6 in / $2.2 out per 1M tokens"),
    ("glm-4.7", "GLM-4.7", "chat", "≈$0.6 in / $2.2 out per 1M tokens"),
    ("glm-5.2", "GLM-5.2 (flagship)", "chat", "≈$1.4 in / $4.4 out per 1M tokens"),
    ("glm-5.1", "GLM-5.1", "chat", "≈$1.4 in / $4.4 out per 1M tokens"),
    ("glm-5-turbo", "GLM-5 Turbo", "chat", "≈$1.2 in / $4.0 out per 1M tokens"),
    ("glm-4.7-flashx", "GLM-4.7 FlashX (cheap)", "chat", "≈$0.07 in / $0.4 out per 1M tokens"),
    ("glm-4.7-flash", "GLM-4.7 Flash (free)", "chat", "Free"),
    ("glm-4.5-air", "GLM-4.5 Air (lightweight)", "chat", "≈$0.2 in / $1.1 out per 1M tokens"),
    ("glm-4.5-flash", "GLM-4.5 Flash (free)", "chat", "Free"),
    # Vision-capable chat models (image understanding / OCR via chat completions).
    ("glm-5v-turbo", "GLM-5V Turbo (vision)", "chat", "≈$1.2 in / $4.0 out per 1M tokens"),
    ("glm-4.6v", "GLM-4.6V (vision)", "chat", "≈$0.3 in / $0.9 out per 1M tokens"),
]

# Curated GLM models for the CLI proxy ("cli_proxy" mode). Z.AI maps the Claude
# tiers to a GLM model server-side via ANTHROPIC_DEFAULT_*_MODEL, so the model
# id here is exported as that mapping (the transport passes no --model, which
# Claude Code rejects for a non-Claude name). Chat only — the CLI is text-only.
ZAI_CLI_MODELS = [
    ("glm-4.6", "GLM-4.6 (recommended)"),
    ("glm-4.7", "GLM-4.7"),
    ("glm-5.2", "GLM-5.2 (flagship)"),
    ("glm-4.5-air", "GLM-4.5 Air (fast)"),
]

# --- Kimi (Moonshot AI) ------------------------------------------------------
# Like Z.AI, Kimi is reachable two ways with the SAME API key:
#   * "api_key"   -> OpenAI-compatible chat at api.moonshot.ai/v1
#                    (/chat/completions, Bearer auth, native tool-calling).
#   * "cli_proxy" -> Moonshot's first-party `kimi` binary (Kimi Code CLI) in
#                    print mode. The key is handed to it through KIMI_API_KEY /
#                    KIMI_BASE_URL; with no key the CLI uses its own `kimi login`
#                    under the account's CLI HOME (see kimi_cli_transport).
# Kimi has no per-model price API, so the rows below carry indicative USD rates
# captured from the public pricing page (2026-08) — verify the live page:
#   https://platform.kimi.ai/docs/pricing/chat
# One source of truth for the endpoint: the transport owns it (it is also the
# KIMI_BASE_URL it hands to the CLI), and the HTTP paths here reuse it.
KIMI_OPENAI_BASE_URL = kimi_cli_transport.KIMI_DEFAULT_BASE_URL

# Curated Kimi chat models for the OpenAI-compatible API ("api_key" mode).
# Editable per account; "Sync models" re-applies this set.
KIMI_MODELS = [
    # (model_id, label, kind, rate)
    ("kimi-k2.6", "Kimi K2.6 (recommended, multimodal)", "chat",
     "≈$0.95 in / $4.00 out per 1M tokens"),
    ("kimi-k3", "Kimi K3 (flagship, 1M context)", "chat",
     "≈$3.00 in / $15.00 out per 1M tokens"),
    ("kimi-k2.7-code", "Kimi K2.7 Code (coding)", "chat",
     "≈$0.95 in / $4.00 out per 1M tokens"),
    ("kimi-k2.7-code-highspeed", "Kimi K2.7 Code — high speed", "chat",
     "≈$0.95 in / $4.00 out per 1M tokens"),
    ("kimi-k2.5", "Kimi K2.5", "chat", "≈$0.60 in / $3.00 out per 1M tokens"),
    # The moonshot-v1 generation is being retired (announced sunset 2026-08-31);
    # kept so an existing integration pinned to one keeps working until then.
    ("moonshot-v1-128k", "Moonshot v1 128k (legacy)", "chat",
     "legacy — retiring 2026-08-31, see pricing page"),
    ("moonshot-v1-32k", "Moonshot v1 32k (legacy)", "chat",
     "legacy — retiring 2026-08-31, see pricing page"),
    ("moonshot-v1-8k", "Moonshot v1 8k (legacy)", "chat",
     "legacy — retiring 2026-08-31, see pricing page"),
]

# Curated Kimi models for the CLI proxy ("cli_proxy" mode). The id is exported
# as KIMI_MODEL_NAME rather than passed to --model, which expects an alias
# declared in the CLI's own [models] config table (see kimi_cli_transport).
# Chat only — the CLI is text-only here.
KIMI_CLI_MODELS = [
    ("kimi-k2.6", "Kimi K2.6 (recommended)"),
    ("kimi-k3", "Kimi K3 (flagship)"),
    ("kimi-k2.7-code", "Kimi K2.7 Code"),
    ("kimi-k2.5", "Kimi K2.5"),
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
            ("zai", "Z.AI (GLM / Zhipu)"),
            ("kimi", "Kimi (Moonshot AI)"),
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
             "CLI — Claude Code for Anthropic, Codex for OpenAI, Kimi Code for "
             "Moonshot, or the Claude binary pointed at Z.AI's endpoint for GLM. "
             "API key calls the provider over HTTP "
             "(OpenAI/Gemini/Cloudflare/Z.AI/Kimi/custom).",
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
        default=1, readonly=True,
        help="Legacy field (kept for migrations). CLI-proxy calls are serialized "
             "host-wide per provider: Claude allows ai.cli_max_concurrency slots, "
             "Codex always exactly 1 (its auth.json is single-writer). Configure "
             "that concurrency in Settings → AI.",
    )
    # --- Usage stats (incremented on every outbound call) --------------------
    request_count = fields.Integer(
        string="Requests", readonly=True, copy=False,
        help="Total outbound AI calls (chat, images, transcriptions) through this "
             "account since creation. Useful for spotting a shared account being "
             "hammered or an abandoned one.",
    )
    last_request_at = fields.Datetime(readonly=True, copy=False)

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
             "(or ERA_AI_CODEX_BIN) for OpenAI, `kimi` on PATH (or "
             "ERA_AI_KIMI_BIN) for Kimi.",
    )
    cli_home_dir = fields.Char(
        string="CLI HOME",
        default="/opt/odoo",
        help="HOME directory whose connected-account auth the CLI should use "
             "(~/.claude for Claude, ~/.codex for Codex, ~/.kimi for Kimi). "
             "Ignored once you link an account below (the linked credentials "
             "take over).",
    )
    cli_extra_args = fields.Char(
        string="CLI extra arguments", groups=MANAGER_GROUP,
        help="Appended verbatim to the CLI invocation (shlex-split). Manager-only: "
             "these flags shape what the subprocess may do.",
    )
    cli_tools_enabled = fields.Boolean(
        string="Allow agent tools",
        default=True,
        help="Let agents run their Odoo tools (e.g. Ask AI's database lookups) "
             "through this CLI account, via a JSON tool-call loop. Each tool "
             "round is one extra CLI call — disable to keep the account strictly "
             "single-shot chat. Tools always execute in Odoo with the requesting "
             "user's own access rights; the CLI only sees text.",
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
    cli_link_state = fields.Selection(
        selection=[
            ("none", "Not linked"),
            ("active", "Linked"),
            ("stale", "Linked (renewing)"),
            ("expired", "Link expired — re-link required"),
        ],
        string="Link status", compute="_compute_cli_oauth_linked",
        help="On-disk state of the linked subscription credentials. "
             "'stale' means the short-lived access token lapsed but a refresh "
             "token remains, so the CLI renews it in place. 'expired' means the "
             "login is dead and a manager must re-link — the server will NOT fall "
             "back to its own local login for an account that was linked.",
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
            state = info.get("state") or "none"
            rec.cli_link_state = state
            rec.cli_oauth_linked = bool(info.get("linked"))
            renewing = state == "stale"
            if state == "none":
                rec.cli_oauth_label = ""
            elif state == "expired":
                # A dead link is surfaced explicitly (red banner in the form) so
                # it gets re-linked instead of silently drifting onto the
                # server's own login.
                rec.cli_oauth_label = _("Link expired — re-link required")
            elif rec.provider == "openai":
                plan = info.get("subscription") or ""
                base = _("ChatGPT %s linked", plan) if plan else _("ChatGPT account linked")
                rec.cli_oauth_label = _("%s (renewing)", base) if renewing else base
            else:
                sub = info.get("subscription") or "subscription"
                base = _("Claude %s linked", sub)
                rec.cli_oauth_label = _("%s (renewing)", base) if renewing else base

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
                    "(Claude CLI), OpenAI (Codex CLI), Z.AI (GLM) and Kimi "
                    "(Kimi Code CLI) providers."
                ))

    # Flags an admin must NOT pass via cli_extra_args: they override the
    # sandbox / capability restrictions the transports enforce deliberately, or
    # smuggle in arbitrary code/tool execution on a shared subscription account.
    # Kept as a denylist (not an allowlist) so benign flags like --max-tokens or
    # --model keep working without a code change each time the CLIs evolve.
    _CLI_DENYLIST_TOKENS = (
        "--dangerously-skip-permissions", "--dangerously", "--bypass",
        "--allow-write", "--allow-net", "--allow-all", "--full-auto",
        "--yolo", "--y", "-y", "--no-sandbox", "--disable-sandbox",
        # Claude Code permission grants / tool overrides
        "--allowedTools", "--allow-tools", "--disallowedTools",
        "--permission-mode", "--add-dir", "--resume", "--continue",
        # Codex capability re-enablers
        "--enable", "--config", "--config-file", "-c",
        "--ask-for-approval", "--sandbox-mode",
        "--project-doc", "--project-doc-max-bytes",
        # Kimi (Kimi Code CLI) capability re-enablers: approval bypasses, the
        # workspace/tool fences the transport sets, and session resumption
        # (which would replay another conversation's context into this call).
        "--yes", "--auto-approve", "--afk",
        "--work-dir", "-w", "--skills-dir",
        "--agent", "--agent-file",
        "--mcp-config", "--mcp-config-file",
        "--max-ralph-iterations",
        "--session", "-S", "-r", "-C",
    )

    @api.constrains("cli_extra_args")
    def _check_cli_extra_args(self):
        # The transports shlex-split this field on every call — catch an
        # unbalanced quote at save time instead of failing every AI request.
        # Also enforce a denylist so a manager can't widen what the subprocess
        # is allowed to do (the sandbox flags are load-bearing on a shared
        # ChatGPT/Claude subscription account).
        for rec in self:
            args = rec.sudo().cli_extra_args
            if not args:
                continue
            try:
                tokens = shlex.split(args)
            except ValueError as exc:
                raise ValidationError(_("Invalid CLI extra arguments: %s", exc))
            for token in tokens:
                # Match the flag even when written as --flag=value.
                flag = token.split("=", 1)[0]
                if flag in self._CLI_DENYLIST_TOKENS:
                    raise ValidationError(_(
                        "The CLI argument '%s' is blocked for security: it can "
                        "override the sandbox or re-enable capabilities on the "
                        "connected account. Remove it from 'CLI extra arguments'.",
                        flag))

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
            # Z.AI cli_proxy reuses the Claude (anthropic_cli) transport — the
            # same `claude` binary, redirected to Z.AI via _cli_cfg's env keys.
            # Kimi has its own first-party binary, hence its own token.
            if self.provider == "openai":
                return "openai_cli"
            if self.provider == "kimi":
                return "kimi_cli"
            return "anthropic_cli"
        if self.provider == "custom":
            return "custom_llm"
        return self.provider  # openai / google / anthropic / cloudflare / zai / kimi

    _PROVIDER_DEFAULT_MODEL = {
        "anthropic": "claude-opus-4-8",
        "openai": "gpt-4o",
        "google": "gemini-2.5-flash",
        "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
        "zai": "glm-4.6",
        "kimi": "kimi-k2.6",
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
        if len(prompt or "") > 4000:
            _logger.warning(
                "era_ai_accounts: image prompt truncated from %d to 4000 chars "
                "on account '%s' (id=%s).", len(prompt or ""), self.name, self.id)
        prompt = (prompt or "")[:4000]
        self._log_request()
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

    def transcribe(self, audio, model=None, language=None, filename="audio.mp3", prompt=None):
        """Transcribe speech audio to text and return the transcript string, or raise.

        OpenAI (Whisper / gpt-4o-transcribe) only — the Claude/Codex CLI proxies
        are text-in/text-out and have no speech endpoint. ``audio`` is the raw
        file bytes; ``language`` is an optional ISO-639-1 hint and ``prompt`` an
        optional context string to steer spelling/vocabulary.
        """
        self.ensure_one()
        self._assert_usable()
        if not audio:
            raise UserError(_("No audio was supplied to transcribe."))
        self._log_request()
        if self.provider == "openai":
            if self.auth_mode == "cli_proxy":
                raise UserError(_(
                    "Transcription is not available through the Codex CLI proxy "
                    "(account '%s') — use an OpenAI API-key account for audio.",
                    self.name))
            return self._transcribe_openai(audio, model, language, filename, prompt)
        raise UserError(_(
            "Audio transcription is supported for OpenAI accounts "
            "(account '%s' is '%s').", self.name, self.provider))

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

    def _transcribe_openai(self, audio, model, language, filename, prompt):
        """OpenAI speech-to-text (gpt-4o-transcribe / gpt-4o-mini-transcribe /
        whisper-1) via the multipart ``/audio/transcriptions`` endpoint."""
        key = self._get_secret()
        if not key:
            raise UserError(_("Set the OpenAI API key on account '%s'.", self.name))
        model = model or "gpt-4o-transcribe"
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        # Default response_format is JSON ({"text": ...}) for every model, incl.
        # gpt-4o-transcribe which ONLY supports json — so we never override it.
        data = {"model": model}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt[:2000]
        # Multipart: pass the file via files= and let requests set the
        # Content-Type (with boundary). Never force application/json here.
        files = {"file": (filename or "audio.mp3", audio)}
        try:
            timeout = int(self.env["ir.config_parameter"].sudo().get_param(
                "ai.openai_transcribe_timeout", "120"))
        except (TypeError, ValueError):
            timeout = 120
        try:
            resp = requests.post(
                base + "/audio/transcriptions",
                headers={"Authorization": "Bearer %s" % key},
                data=data, files=files, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            raise UserError(_("OpenAI transcription request failed: %s", exc))
        if resp.status_code >= 400:
            raise UserError(_("OpenAI transcription error (%(code)s): %(detail)s",
                              code=resp.status_code, detail=(resp.text or "")[:400]))
        if "application/json" in (resp.headers.get("Content-Type") or ""):
            try:
                text = resp.json().get("text")
            except ValueError as exc:
                raise UserError(_("OpenAI returned an unexpected transcript response: %s", exc))
        else:
            text = resp.text  # whisper-1 with response_format=text (not requested here)
        if not text:
            raise UserError(_("OpenAI returned an empty transcript."))
        return text

    # --------------------------------------------------------------- public API
    def generate_text(self, prompt, system="", model=None, temperature=0.2):
        """Generate a text/chat completion through this account; return the string.

        Provider-agnostic: works for the Claude CLI proxy, OpenAI/Google/Anthropic
        keys, Cloudflare Workers AI, and custom OpenAI-compatible endpoints. Lets
        any module use an account for content without wiring up an ai.agent.
        """
        self.ensure_one()
        self._assert_usable()
        self._log_request()
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
            if self.provider == "zai":
                return ZAI_CLI_MODELS[0][0]
            if self.provider == "kimi":
                return KIMI_CLI_MODELS[0][0]
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
                # makedirs masks mode by umask and only sets it on the leaf;
                # force the whole private chain (…/era_ai_accounts, …/cli,
                # …/<id>, …/<subdir>) to 0700 so a linked account's
                # presence/ids aren't world-listable (the token file is 0600).
                cli_root = os.path.dirname(home)
                for p in (os.path.dirname(cli_root), cli_root, home, path):
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
        """Read the linked credentials; return {state, linked, subscription, ...}.

        ``state`` is the single source of truth for how the account is linked:

        * ``active``  — a usable access token is present (and unexpired);
        * ``stale``   — the short-lived access token lapsed/emptied but a refresh
                        token remains, so the first-party CLI can renew it in
                        place. Still counts as linked and still routes to the
                        managed dir (that renewal is exactly how a link stays
                        alive across the access token's ~hourly expiry);
        * ``expired`` — the credential file exists (this account WAS linked) but
                        nothing usable remains — a manager must re-link;
        * ``none``    — no credential file was ever written (the account uses the
                        server's ambient login on purpose).

        ``linked`` is kept for existing callers/views as ``state in
        {active, stale}``.
        """
        self.ensure_one()
        profile = _CLI_PROFILES.get(self.provider)
        if not profile or not profile.get("cred_file"):
            # Key-authenticated CLI providers (Z.AI, Kimi): there is no in-app
            # subscription link to read. Answer 'none' rather than falling
            # through to the Anthropic layout and probing another provider's
            # credential file (Z.AI has no _CLI_PROFILES entry at all; Kimi has
            # one for its config dir, but no credential file).
            return {"state": "none", "linked": False}
        try:
            with open(self._cli_credentials_path(), "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"state": "none", "linked": False}
        if not isinstance(data, dict):
            return {"state": "none", "linked": False}
        info = (self._codex_oauth_info(data) if self.provider == "openai"
                else self._anthropic_oauth_info(data))
        info["linked"] = info.get("state") in ("active", "stale")
        return info

    @staticmethod
    def _anthropic_oauth_info(data):
        """Interpret a Claude Code ``.credentials.json`` into a link ``state``."""
        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            # A file in some other shape: treat as never-linked (ambient login).
            return {"state": "none"}
        access = oauth.get("accessToken") or ""
        refresh = oauth.get("refreshToken") or ""
        expires_at = oauth.get("expiresAt") or 0
        now_ms = time.time() * 1000
        if access and (not expires_at or expires_at > now_ms):
            state = "active"
        elif refresh:
            # Access token gone/expired but renewable: the CLI refreshes on use.
            state = "stale"
        else:
            state = "expired"
        return {
            "state": state,
            "subscription": oauth.get("subscriptionType") or "",
            "expires_at": expires_at,
            "refresh_expires_at": oauth.get("refreshTokenExpiresAt") or 0,
        }

    @staticmethod
    def _codex_oauth_info(data):
        """Interpret a Codex ``auth.json`` into a link ``state``."""
        tokens = data.get("tokens") or {}
        # auth_mode is "chatgpt" for subscription auth; absent on some older
        # files — only an explicit API-key mode disqualifies.
        if not isinstance(tokens, dict) or (data.get("auth_mode") or "chatgpt") != "chatgpt":
            return {"state": "none"}
        access = tokens.get("access_token") or ""
        refresh = tokens.get("refresh_token") or ""
        if access:
            state = "active"
        elif refresh:
            # codex renews the access token itself from the refresh token.
            state = "stale"
        elif tokens:
            state = "expired"  # a ChatGPT login that lost its tokens
        else:
            state = "none"
        return {
            "state": state,
            "subscription": _codex_plan_from_id_token(tokens.get("id_token") or ""),
            "expires_at": 0,  # codex tracks freshness itself via last_refresh
        }

    def _cli_is_linked(self):
        self.ensure_one()
        return bool(self.sudo()._cli_oauth_info().get("linked"))

    def _cli_link_state(self):
        self.ensure_one()
        return self.sudo()._cli_oauth_info().get("state") or "none"

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

    def _cli_drop_linked_credentials(self):
        """Remove this account's linked credential file + pending login state."""
        self.ensure_one()
        try:
            os.remove(self.sudo()._cli_credentials_path())
        except OSError:
            pass
        self.env["ir.config_parameter"].sudo().set_param(_PKCE_PARAM % self.id, "")
        self._codex_device_login_cancel()

    def _oauth_logout(self):
        """Remove the linked account's stored credentials (forces re-link)."""
        self.ensure_one()
        self._assert_oauth_manager()
        self._cli_drop_linked_credentials()
        return True

    def write(self, vals):
        # Changing the provider re-keys the credential layout (_cli_profile):
        # drop the OLD provider's linked credentials first, or they would be
        # stranded on disk — invisible to 'Disconnect', yet silently picked up
        # again if the provider is ever switched back. Manager-gated via ACL
        # (only AI Account Managers can write era.ai.account).
        if "provider" in vals:
            for rec in self:
                if rec.id and rec.provider != vals["provider"]:
                    rec.sudo()._cli_drop_linked_credentials()
        res = super().write(vals)
        # The CLI proxies are text-only: flipping an account to cli_proxy must
        # not leave earlier image/embedding rows (e.g. synced gpt-image-1)
        # selectable in other modules' pickers — archive them, like a sync would.
        if vals.get("auth_mode") == "cli_proxy":
            stale = self.with_context(active_test=False).model_ids.filtered(
                lambda m: m.kind != "chat" and m.active)
            if stale:
                _logger.info(
                    "era_ai_accounts: archiving %d non-chat model row(s) on "
                    "CLI-proxy switch: %s", len(stale), stale.mapped("model_id"))
                stale.active = False
        return res

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

    # --------------------------------------- ChatGPT (Codex device-code) login
    def _codex_device_state(self):
        """The pending device login {pid, out, started_at}, or {}."""
        self.ensure_one()
        raw = self.env["ir.config_parameter"].sudo().get_param(_DEVICE_PARAM % self.id)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _codex_device_clear(self):
        self.ensure_one()
        self.env["ir.config_parameter"].sudo().set_param(_DEVICE_PARAM % self.id, "")

    def _codex_device_login_start(self):
        """Kick off ``codex login --device-auth``; return {url, code, raw}.

        The simple path for linking a ChatGPT account: codex prints a
        verification URL + one-time code (valid ~15 minutes), the manager
        approves from any browser/phone, and codex writes auth.json into the
        managed CODEX_HOME itself. Requires device codes to be enabled in the
        ChatGPT account's security settings.
        """
        self.ensure_one()
        self._assert_oauth_manager()
        if self.auth_mode != "cli_proxy" or self.provider != "openai":
            raise UserError(_(
                "Device login only applies to OpenAI CLI-proxy accounts."))
        # Supersede any previous attempt (its one-time code dies with it).
        self._codex_device_login_cancel()
        cfg = {
            "cli_path": self.cli_path or False,
            "home_dir": self._cli_managed_home(),
            "config_dir": self._cli_managed_config_dir(create=True),
        }
        pid, out_path = codex_cli_transport.device_login_start(cfg)
        self.env["ir.config_parameter"].sudo().set_param(
            _DEVICE_PARAM % self.id,
            json.dumps({"pid": pid, "out": out_path, "started_at": time.time()}))
        # codex contacts the auth server before printing the URL/code — poll its
        # output briefly (worst case ~5s) so the wizard shows the code right away.
        url = code = raw = ""
        for _attempt in range(20):
            time.sleep(0.25)
            raw = codex_cli_transport.device_login_read(out_path)
            url, code = codex_cli_transport.parse_device_login(raw)
            if url and code:
                break
            if not codex_cli_transport.pid_is_pending_login(pid):
                break
        if url and code:
            return {"url": url, "code": code, "raw": raw}
        self._codex_device_clear()
        detail = (raw or "").strip()[-300:] or _("no output from the codex CLI")
        raise UserError(_(
            "Could not start the device login: %s\n\nMake sure device codes are "
            "enabled in the ChatGPT account's security settings, or paste "
            "auth.json below instead.", detail))

    def _codex_device_login_status(self):
        """Poll a pending device login: 'linked' | 'pending' | 'failed:<detail>'."""
        self.ensure_one()
        if self._cli_is_linked():
            self._codex_device_clear()
            return "linked"
        state = self._codex_device_state()
        if not state:
            return "failed:%s" % _("No device login in progress — start one first.")
        if codex_cli_transport.pid_is_pending_login(state.get("pid")):
            return "pending"
        # Process ended without writing auth.json: denied, expired, or errored.
        raw = codex_cli_transport.device_login_read(state.get("out") or "")
        self._codex_device_clear()
        return "failed:%s" % ((raw or "").strip()[-300:] or _("the login attempt ended"))

    def _codex_device_login_cancel(self):
        self.ensure_one()
        state = self._codex_device_state()
        if state.get("pid"):
            codex_cli_transport.device_login_kill(state["pid"])
        self._codex_device_clear()

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
        # Remove any linked-account credential dir so deleting an account leaves no
        # orphaned tokens on disk (best-effort; never blocks the delete).
        for rec in self:
            try:
                shutil.rmtree(rec._cli_managed_home(), ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            self.env["ir.config_parameter"].sudo().set_param(_PKCE_PARAM % rec.id, "")
            rec._codex_device_login_cancel()
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
        # credentials dir; a never-linked account falls back to the ambient
        # HOME-based login on purpose. The one case we must NOT treat as
        # "ambient" is a link that was established and then died ('expired'):
        # silently borrowing the server operator's own login there hides the
        # dead link and spends the wrong subscription. Fail loudly instead so a
        # manager re-links (an ICP escape hatch keeps the old fall-back if a
        # site truly wants zero-downtime while a re-link is pending).
        link_state = self._cli_link_state()
        if link_state in ("active", "stale"):
            home_dir = self._cli_managed_home()
            # create=True: guarantee the dir exists for CLAUDE_CONFIG_DIR /
            # CODEX_HOME even if it was removed out from under us between the
            # link check and the call.
            config_dir = self._cli_managed_config_dir(create=True)
        elif link_state == "expired":
            if _b("era_ai_accounts.cli_link_strict", True):
                raise UserError(_(
                    "The linked subscription for AI account '%s' has expired and "
                    "can no longer sign in. An AI Account Manager must re-link it "
                    "(open the account and click “Login with Claude” / “Re-link "
                    "account”). The server is intentionally not falling back to "
                    "its own local login for a shared account that was linked.",
                    self.name))
            _logger.warning(
                "era_ai_accounts: account '%s' (id=%s) has an EXPIRED link; "
                "falling back to the ambient login at %s because "
                "era_ai_accounts.cli_link_strict is off. Re-link it to restore "
                "the shared account.", self.name, self.id, self.cli_home_dir or "/opt/odoo")
            home_dir = self.cli_home_dir or "/opt/odoo"
            config_dir = False
        else:  # "none" — never linked; use the server's ambient login by design.
            home_dir = self.cli_home_dir or "/opt/odoo"
            config_dir = False

        cfg = {
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
        # Z.AI (GLM) over the CLI: redirect the Claude binary at Z.AI's
        # Anthropic-compatible endpoint with the GLM Coding Plan key. The
        # transport exports these (ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN +
        # the GLM model mapping) instead of using the connected-account OAuth.
        if self.provider == "zai":
            token = self._get_secret()
            if not token:
                raise UserError(_(
                    "Set the Z.AI API key on account '%s' (used as the CLI's "
                    "ANTHROPIC_AUTH_TOKEN).", self.name))
            cfg["anthropic_base_url"] = (self.base_url or ZAI_ANTHROPIC_BASE_URL).strip()
            cfg["anthropic_auth_token"] = token
        # Kimi over its own CLI: the key travels in the environment (never a
        # config file), and the transport regenerates config.toml + SYSTEM.md in
        # this isolated KIMI_CODE_HOME before every call — so the account gets a
        # managed dir unconditionally, not only when a subscription is linked.
        # A key is required: `kimi login` is an interactive device-code flow and
        # cannot be driven from here.
        if self.provider == "kimi":
            token = self._get_secret()
            if not token:
                raise UserError(_(
                    "Set the Kimi API key on account '%s' — the Kimi CLI runs "
                    "non-interactively here and cannot use its own browser "
                    "login.", self.name))
            cfg["kimi_api_key"] = token
            cfg["kimi_base_url"] = (
                self.base_url or kimi_cli_transport.KIMI_DEFAULT_BASE_URL).strip()
            cfg["config_dir"] = self._cli_managed_config_dir(create=True)
        return cfg

    def _assert_usable(self):
        self.ensure_one()
        if self.kill_switch:
            raise UserError(_("AI account '%s' is disabled (kill switch).", self.name))
        if not self.active:
            raise UserError(_("AI account '%s' is archived.", self.name))

    def _log_request(self):
        """Increment per-account usage counters. Uses a raw UPDATE to avoid
        concurrent-write conflicts when multiple users share one account."""
        self.ensure_one()
        try:
            self.env.cr.execute(
                "UPDATE era_ai_account SET request_count = COALESCE(request_count, 0) + 1, "
                "last_request_at = NOW() WHERE id = %s",
                (self.id,))
        except Exception:  # noqa: BLE001 — never let a stats counter block a call
            _logger.debug("era_ai_accounts: could not update request_count", exc_info=True)

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
                # The raise below rolls back this transaction — persist the
                # error state through a separate cursor so the Status tab still
                # shows what failed.
                detail = str(exc)[:2000]
                try:
                    with self.pool.cursor() as cr:
                        rec.with_env(rec.env(cr=cr)).write(
                            {"state": "error", "last_error": detail})
                except Exception:  # noqa: BLE001 - never mask the real error
                    _logger.exception("era_ai_accounts: could not persist validation error")
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
            if self.provider == "kimi":
                # `kimi --version` proves the binary runs; the key is checked
                # separately over HTTP (the same key serves both Kimi surfaces).
                # There is no token-free credential probe in the CLI itself.
                kimi_cli_transport.check_cli(self._cli_cfg())
                self._http_get_json(
                    (self.base_url or KIMI_OPENAI_BASE_URL).rstrip("/") + "/models",
                    {"Authorization": "Bearer %s" % self._get_secret()}, 30)
                return True
            binary = llm_cli_transport.resolve_cli_binary(self.cli_path or None)
            if not binary:
                raise UserError(_("Claude CLI binary not found on this server."))
            # Cheap liveness check: the CLI prints its version without any model call.
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                raise UserError(_("Claude CLI not runnable: %s", (out.stderr or "").strip()))
            if self.provider == "zai":
                # The binary is fine; also prove the GLM Coding Plan key is
                # accepted (token-free) via Z.AI's OpenAI-style /models listing —
                # the same key authenticates both Z.AI surfaces.
                token = self._get_secret()
                if not token:
                    raise UserError(_("Set the Z.AI API key before validating."))
                self._http_get_json(
                    ZAI_OPENAI_BASE_URL + "/models",
                    {"Authorization": "Bearer %s" % token}, 30)
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
        if self.provider in ("zai", "kimi"):
            # OpenAI-compatible /models listing — token-free key check.
            token = self._get_secret()
            if not token:
                raise UserError(_("Set the API key before validating."))
            default_base = ZAI_OPENAI_BASE_URL if self.provider == "zai" else KIMI_OPENAI_BASE_URL
            base = (self.base_url or default_base).rstrip("/")
            self._http_get_json(base + "/models", {"Authorization": "Bearer %s" % token}, 30)
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
            if self.provider == "openai":
                cli_models = CODEX_CLI_MODELS
            elif self.provider == "zai":
                cli_models = ZAI_CLI_MODELS
            elif self.provider == "kimi":
                cli_models = KIMI_CLI_MODELS
            else:
                cli_models = CLAUDE_CLI_MODELS
            rows = [(mid, label, "chat", "") for mid, label in cli_models]
        elif self.provider == "cloudflare":
            # Cloudflare has no per-model price API, so use the curated catalog with
            # the published Neuron rates baked in (see CLOUDFLARE_MODELS).
            rows = list(CLOUDFLARE_MODELS)
        elif self.provider == "zai":
            # Z.AI has no per-model price API either; ship the curated GLM catalog
            # with indicative USD rates (editable per account).
            rows = list(ZAI_MODELS)
        elif self.provider == "kimi":
            # Same for Kimi: /models lists ids without prices, so ship the
            # curated catalog with indicative USD rates (editable per account).
            rows = list(KIMI_MODELS)
        elif self.provider == "openai":
            # /models lists chat models only — add the image models so they can be
            # picked for cover generation (gpt-image-1 / DALL·E).
            rows = [(mid, label, kind, "") for mid, label, kind in self._http_list_models()]
            rows += list(OPENAI_IMAGE_MODELS)
        else:
            rows = [(mid, label, kind, "") for mid, label, kind in self._http_list_models()]
        # active_test=False: archived rows must stay in the upsert map, or
        # re-creating a model that was previously deactivated (provider switch,
        # curated-list churn, admin unchecking a row) trips the SQL unique
        # constraint and aborts the whole sync.
        existing = {(m.model_id, m.kind): m
                    for m in self.with_context(active_test=False).model_ids}
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
        # openai / kimi / custom (OpenAI-compatible)
        default_base = KIMI_OPENAI_BASE_URL if self.provider == "kimi" else "https://api.openai.com/v1"
        base = (self.base_url or default_base).rstrip("/")
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
