"""Local Kimi CLI-proxy transport for Kimi (Moonshot AI) accounts.

Routes a chat request through Moonshot's first-party ``kimi`` binary (Kimi Code
CLI) in **print mode** — its documented non-interactive / automation mode. The
CLI performs the model call itself, either under the credentials of its own
``~/.kimi`` login (``kimi login``) or under the API key this account exports to
it; we never read or replay a stored OAuth token against the API ourselves.

Locking the invocation down to pure text
----------------------------------------
``--print`` implicitly enables ``--yolo``: every tool call — file writes and
shell commands included — is auto-approved. That is unacceptable for a server
driven by end-user chat prompts, so each call is fenced by three independent
measures, none of which is load-bearing on its own:

* ``--config`` supplies an inline tool **allowlist that matches nothing** —
  kimi's ``[tools].enabled`` acts as an allowlist when non-empty, and a name
  matching no registered tool matches nothing, so the model is offered no tools;
* ``--max-steps-per-turn 1`` bounds the agent loop to a single step;
* ``--work-dir`` points at a **fresh empty directory** created per call, so file
  tools have nothing to reach and no project context (AGENTS.md / KIMI.md, the
  Odoo tree) is picked up.

If a future kimi version rejects one of these flags the call fails loudly with a
non-zero exit rather than silently running unfenced — but re-verify them after a
CLI upgrade all the same.

Model selection goes through ``KIMI_MODEL_NAME`` rather than ``--model``: kimi's
``--model`` takes an *alias* declared in the config's ``[models]`` table, while
the env vars configure the built-in ``kimi`` provider directly — the same
pattern as the Z.AI transport's ``ANTHROPIC_DEFAULT_*_MODEL`` mapping.

Throttling (slot semaphore + size-scaled gap) reuses the Claude transport's
helpers under its own lock-file namespace, so Kimi, Claude and Codex calls never
queue behind one another. With an account API key the CLI is stateless and may
use the configured concurrency; falling back to its own on-disk login, it is
clamped to one call at a time (a token refresh rewrites ``~/.kimi``).
"""
import glob
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile

from odoo import _
from odoo.exceptions import UserError

from .llm_cli_transport import (  # shared throttle/limits machinery
    _compute_gap,
    _enforce_gap,
    _global_slot,
    _preexec_unlimit_as,
    _record_call_end,
    resource,
)

_logger = logging.getLogger(__name__)

# Per-provider throttle namespace (see llm_cli_transport for the Claude pool).
_LOCK_SLOT = "era_ai_cli_proxy.kimi.%d.lock"
_STATE_FILE = "era_ai_cli_proxy.kimi.last"

# Default endpoint of the built-in ``kimi`` provider (OpenAI-compatible).
KIMI_DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"

# Search globs for a ``kimi`` binary that is not on PATH. kimi-cli ships on PyPI
# and is normally installed with ``uv tool install kimi-cli``, which links the
# entry point into ~/.local/bin. Newest mtime wins, like the other transports.
_KIMI_GLOBS = [
    "/opt/odoo/.local/bin/kimi",
    "/usr/local/bin/kimi",
    os.path.expanduser("~/.local/bin/kimi"),
    os.path.expanduser("~/.local/share/uv/tools/kimi-cli/bin/kimi"),
    os.path.expanduser("~/.npm-global/bin/kimi"),
]

# An allowlist entry that deliberately matches no registered tool. kimi warns
# about the unknown name and ends up with an empty tool set — which is exactly
# what a pure chat completion needs.
_NO_TOOLS_SENTINEL = "EraAiAccountsNoTools"
_NO_TOOLS_CONFIG = json.dumps({"tools": {"enabled": [_NO_TOOLS_SENTINEL]}})

# Provider credentials / routing that must never be inherited from the ambient
# environment: a call may only use this account's own key or the CLI's own
# login, never an operator's personal key exported for interactive `kimi` use.
_PURGED_ENV = (
    "KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_MODEL_NAME",
    "KIMI_MODEL_MAX_CONTEXT_SIZE", "KIMI_MODEL_CAPABILITIES",
    "KIMI_MODEL_TEMPERATURE", "KIMI_MODEL_TOP_P",
    "KIMI_MODEL_MAX_COMPLETION_TOKENS", "KIMI_MODEL_MAX_TOKENS",
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
)

# kimi's documented exit codes: 0 success, 1 non-retryable (auth/config/quota),
# 75 retryable (rate limit, timeout, 5xx).
_EXIT_RETRYABLE = 75


def resolve_cli_binary(override=None):
    """Return the path to the ``kimi`` binary, or None if not found.

    Resolution order: explicit ``override`` -> ``$ERA_AI_KIMI_BIN`` -> ``kimi``
    on PATH -> known install locations.
    """
    if override and os.path.exists(override):
        return override
    env_bin = os.getenv("ERA_AI_KIMI_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    on_path = shutil.which("kimi")
    if on_path:
        return on_path
    candidates = []
    for pattern in _KIMI_GLOBS:
        candidates.extend(glob.glob(pattern))
    if not candidates:
        return None
    candidates.sort(key=lambda p: (os.path.getmtime(p), p))
    return candidates[-1]


def _require_binary(cfg):
    binary = resolve_cli_binary(cfg.get("cli_path"))
    if not binary:
        raise UserError(_(
            "The Kimi CLI was not found on this server. Install it (uv tool "
            "install kimi-cli), or set the account's 'CLI binary path' or the "
            "ERA_AI_KIMI_BIN environment variable."))
    return binary


def _build_env(cfg, model=None):
    """Subprocess environment for one kimi call bound to one account.

    ``HOME`` selects which ``~/.kimi`` (config, sessions, ``kimi login``
    credentials) the CLI uses. When the account carries an API key it is
    exported as ``KIMI_API_KEY`` / ``KIMI_BASE_URL``, which configures the
    built-in ``kimi`` provider without touching any config file; without a key
    the CLI falls back to whatever that HOME is logged in as.
    """
    run_env = dict(os.environ)
    run_env["HOME"] = cfg.get("home_dir") or "/opt/odoo"
    for var in _PURGED_ENV:
        run_env.pop(var, None)
    if cfg.get("kimi_api_key"):
        run_env["KIMI_API_KEY"] = cfg["kimi_api_key"]
        run_env["KIMI_BASE_URL"] = cfg.get("kimi_base_url") or KIMI_DEFAULT_BASE_URL
    if model:
        # See the module docstring: the model is chosen here, not via --model.
        run_env["KIMI_MODEL_NAME"] = model
    # A self-update in the middle of an Odoo request would stall or break it.
    run_env["KIMI_CLI_NO_AUTO_UPDATE"] = "1"
    return run_env


def check_cli(cfg, timeout=30):
    """Prove the ``kimi`` binary exists and runs, without spending any tokens.

    Unlike ``codex login status`` there is no credential probe that is
    guaranteed side-effect-free across kimi versions, so this only checks the
    binary; the account's API key is validated separately over HTTP.
    """
    binary = _require_binary(cfg)
    try:
        proc = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=timeout,
            env=_build_env(cfg),
            preexec_fn=_preexec_unlimit_as if resource else None,
        )
    except subprocess.TimeoutExpired:
        raise UserError(_("The Kimi CLI did not respond within %ss.", timeout))
    except Exception as exc:  # noqa: BLE001
        raise UserError(_("Failed to launch the Kimi CLI: %s", exc))
    if proc.returncode != 0:
        raise UserError(_("Kimi CLI not runnable: %s",
                          (proc.stderr or proc.stdout or "").strip()[:200]))
    return True


def cli_complete(cfg, model, system_prompt, user_prompt, timeout=180):
    """Run a single chat completion through ``kimi --print`` and return the text.

    ``cfg`` is the plain dict built by ``era.ai.account._cli_cfg()`` (no ORM
    coupling) — same contract as the Claude and Codex transports.
    """
    binary = _require_binary(cfg)

    # Pure text generation, nothing else — see the module docstring for why each
    # of these is here and why none of them may be dropped.
    args = [
        binary,
        "--print",
        "--output-format", "text",
        "--final-message-only",
        "--config", _NO_TOOLS_CONFIG,
        "--max-steps-per-turn", "1",
    ]
    if cfg.get("extra_args"):
        try:
            args += shlex.split(cfg["extra_args"])
        except ValueError as exc:
            raise UserError(_(
                "Invalid 'CLI extra arguments' on this AI account: %s", exc))

    # kimi has no system-prompt flag; fold the system text into the stdin
    # document as a clearly delimited instructions block ahead of the user turn
    # (same shape as the Codex transport).
    if system_prompt:
        stdin_doc = (
            "<system_instructions>\n%s\n</system_instructions>\n\n%s"
            % (system_prompt, user_prompt or "")
        )
    else:
        stdin_doc = user_prompt or ""

    req_size = len(stdin_doc)
    gap = _compute_gap(cfg, req_size) if cfg.get("gap_enabled", True) else 0.0
    lock_wait = float(cfg.get("lock_wait", 300.0))
    # With an account API key each call is stateless, so the pool may be as wide
    # as the admin configured. Relying on the CLI's own login instead, a token
    # refresh rewrites ~/.kimi — single-writer, so clamp to one at a time.
    slots = int(cfg.get("concurrency", 1) or 1) if cfg.get("kimi_api_key") else 1

    work_dir = tempfile.mkdtemp(prefix="era_ai_kimi_")
    args += ["--work-dir", work_dir]
    try:
        with _global_slot(slots, lock_wait, lock_name=_LOCK_SLOT):
            _enforce_gap(gap, state_file=_STATE_FILE)
            try:
                _logger.info(
                    "era_ai_accounts: invoking Kimi CLI %s (model=%s, req=%dB, gap=%.1fs)",
                    binary, model or "default", req_size, gap)
                proc = subprocess.run(
                    args,
                    input=stdin_doc,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=_build_env(cfg, model),
                    cwd=work_dir,
                    check=False,
                    preexec_fn=_preexec_unlimit_as if resource else None,
                )
            except subprocess.TimeoutExpired:
                raise UserError(_("The AI request timed out after %ss.", timeout))
            except UserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise UserError(_("Failed to launch the Kimi CLI: %s", exc))
            finally:
                # Stamp the end time even on failure, so the gap also spaces out
                # retries after an error/timeout.
                _record_call_end(state_file=_STATE_FILE)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return _parse_kimi_output(proc.stdout, proc.returncode, proc.stderr)


def _parse_kimi_output(stdout, returncode, stderr):
    """Return the final assistant message, or raise a clean UserError.

    ``--final-message-only`` in text mode prints exactly that message, so the
    exit code is the authoritative success signal: 0 succeeded, 75 is a
    retryable condition (rate limit / timeout / 5xx), anything else is fatal.
    """
    text = (stdout or "").strip()
    if returncode == 0:
        if not text:
            raise UserError(_("The Kimi CLI returned an empty response."))
        return text
    detail = ((stderr or "").strip() or text)[:500]
    _logger.warning(
        "era_ai_accounts: Kimi CLI exited %s: %s", returncode, detail)
    if returncode == _EXIT_RETRYABLE:
        raise UserError(_(
            "The Kimi service is temporarily unavailable (rate limit or "
            "timeout). Please retry in a moment. Details: %s",
            detail or "no details"))
    raise UserError(_("Kimi CLI error: %s", detail or "unknown error"))
