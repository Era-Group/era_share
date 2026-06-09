import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..utils import crypto
from ..utils import llm_cli_transport

_logger = logging.getLogger(__name__)

MANAGER_GROUP = "era_ai_accounts.group_ai_account_manager"

# Curated chat models for the Claude CLI-proxy (the CLI has no list endpoint).
# Use the CLI's *aliases*: `claude --model` resolves them to the latest model of
# each tier the connected account supports, so they never go stale across version
# bumps. Full ids (e.g. claude-opus-4-8) also work if you prefer pinning.
CLAUDE_CLI_MODELS = [
    ("opus", "Claude Opus (latest)"),
    ("sonnet", "Claude Sonnet (latest)"),
    ("haiku", "Claude Haiku (latest)"),
]


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
        help="CLI proxy routes calls through the locally-authenticated Claude CLI "
             "(no API key, no per-token billing). API key calls the provider over HTTP.",
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
        string="Secret stored", compute="_compute_secret_is_set", store=True, compute_sudo=True,
    )
    secret_masked = fields.Char(
        string="Stored key", compute="_compute_secret_masked", compute_sudo=True,
    )

    # --- API-key transport config -------------------------------------------
    base_url = fields.Char(help="Base URL for an OpenAI-compatible endpoint (custom provider).")
    auth_header = fields.Char(default="Authorization")
    auth_prefix = fields.Char(default="Bearer")
    referer = fields.Char(help="Optional HTTP-Referer header (custom/OpenRouter).")
    title = fields.Char(help="Optional X-Title header (custom/OpenRouter).")

    # --- CLI-proxy transport config -----------------------------------------
    cli_path = fields.Char(
        string="CLI binary path",
        help="Optional override for the AI CLI binary. Auto-detected from the "
             "Claude Code extension when empty.",
    )
    cli_home_dir = fields.Char(
        string="CLI HOME",
        default="/opt/odoo",
        help="HOME directory whose connected-account auth the CLI should use.",
    )
    cli_extra_args = fields.Char(string="CLI extra arguments")

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

    # ---------------------------------------------------------------- constraints
    @api.constrains("auth_mode", "provider")
    def _check_auth_mode(self):
        for rec in self:
            if rec.auth_mode == "cli_proxy" and rec.provider != "anthropic":
                raise ValidationError(_(
                    "The local CLI proxy is currently only available for the "
                    "Anthropic (Claude) provider."
                ))

    # ------------------------------------------------------------------- secrets
    def _get_secret(self):
        self.ensure_one()
        return crypto.decrypt_secret(self.env, self.sudo().secret_encrypted)

    # --------------------------------------------------------------- transport cfg
    def _service_provider(self):
        """Provider token used to build a LLMApiService for this account."""
        self.ensure_one()
        if self.auth_mode == "cli_proxy":
            return "anthropic_cli"
        if self.provider == "custom":
            return "custom_llm"
        return self.provider  # openai / google / anthropic

    _PROVIDER_DEFAULT_MODEL = {
        "anthropic": "claude-opus-4-8",
        "openai": "gpt-4o",
        "google": "gemini-2.5-flash",
    }

    def _preferred_chat_model(self):
        """Preferred default model id for this account, by provider + auth mode.

        The CLI proxy uses the version-proof alias 'opus'; the Anthropic HTTP API
        needs a full id (aliases are a CLI-only convenience)."""
        self.ensure_one()
        if self.auth_mode == "cli_proxy" and self.provider == "anthropic":
            return "opus"
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

        try:
            concurrency = max(1, int(float(param.get_param("ai.cli_max_concurrency", "1"))))
        except (TypeError, ValueError):
            concurrency = 1

        return {
            "account_id": self.id,
            "cli_path": self.cli_path or False,
            "home_dir": self.cli_home_dir or "/opt/odoo",
            "extra_args": self.cli_extra_args or False,
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
            binary = llm_cli_transport.resolve_cli_binary(self.cli_path or None)
            if not binary:
                raise UserError(_("Claude CLI binary not found on this server."))
            # Cheap liveness check: the CLI prints its version without any model call.
            import subprocess  # local import; only used here
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                raise UserError(_("Claude CLI not runnable: %s", (out.stderr or "").strip()))
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
        if self.auth_mode == "cli_proxy":
            pairs = [(mid, label, "chat") for mid, label in CLAUDE_CLI_MODELS]
        else:
            pairs = self._http_list_models()
        existing = {(m.model_id, m.kind): m for m in self.model_ids}
        seen = set()
        for model_id, label, kind in pairs:
            seen.add((model_id, kind))
            if (model_id, kind) in existing:
                existing[(model_id, kind)].write({"label": label, "active": True})
            else:
                Model.create({
                    "account_id": self.id, "model_id": model_id,
                    "label": label, "kind": kind,
                })
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
