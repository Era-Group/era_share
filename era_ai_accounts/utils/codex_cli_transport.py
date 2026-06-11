"""Local Codex CLI-proxy transport for OpenAI (ChatGPT account) accounts.

Routes a chat request through OpenAI's first-party ``codex`` binary, signed in
with a ChatGPT subscription (no API key). The CLI performs the model call under
its own OAuth auth and refreshes its tokens itself; we only point it at the
account's credential directory via ``CODEX_HOME`` — we never read or replay the
tokens against the API ourselves.

The invocation is locked down to **pure text generation**: the exec sandbox is
read-only, the shell tool is disabled, web search is off, user config is
ignored, and no session files are written (``--ephemeral``). Throttling (slot
semaphore + size-scaled gap) is shared with the Claude transport's helpers but
uses its own lock-file namespace, so Claude and Codex calls never queue behind
each other.

Codex tokens rotate on refresh and ``auth.json`` is single-writer: keep
``ai.cli_max_concurrency`` at 1 (the default) — the per-provider slot pool then
guarantees at most one ``codex`` process per host, so a token refresh can never
race another call on the same account.
"""
import glob
import json
import logging
import os
import shlex
import shutil
import subprocess

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
_LOCK_SLOT = "era_ai_cli_proxy.codex.%d.lock"
_STATE_FILE = "era_ai_cli_proxy.codex.last"

# Search globs for a codex binary that is not on PATH (npm global installs and
# the ChatGPT/Codex VS Code extension). Newest mtime wins, like the Claude glob.
_CODEX_GLOBS = [
    "/opt/odoo/.npm-global/bin/codex",
    "/usr/local/bin/codex",
    os.path.expanduser("~/.npm-global/bin/codex"),
    os.path.expanduser("~/.local/share/code-server/extensions/openai.chatgpt-*/bin/codex"),
    os.path.expanduser("~/.vscode-server/extensions/openai.chatgpt-*/bin/codex"),
]


def resolve_cli_binary(override=None):
    """Return the path to the ``codex`` binary, or None if not found.

    Resolution order: explicit ``override`` -> ``$ERA_AI_CODEX_BIN`` -> ``codex``
    on PATH -> known npm/extension install locations.
    """
    if override and os.path.exists(override):
        return override
    env_bin = os.getenv("ERA_AI_CODEX_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    on_path = shutil.which("codex")
    if on_path:
        return on_path
    candidates = []
    for pattern in _CODEX_GLOBS:
        candidates.extend(glob.glob(pattern))
    if not candidates:
        return None
    candidates.sort(key=lambda p: (os.path.getmtime(p), p))
    return candidates[-1]


def _build_env(cfg):
    """Subprocess environment for a codex call bound to one account.

    ``CODEX_HOME`` points at the account's linked credential dir (holding
    ``auth.json``); without a linked account it is *removed* so codex falls back
    to the ambient ``$HOME/.codex`` login, mirroring the Claude transport's
    ambient-HOME behaviour. API keys are stripped so the call can only use the
    connected ChatGPT account's own auth, never key-based billing.
    """
    run_env = dict(os.environ)
    run_env["HOME"] = cfg.get("home_dir") or "/opt/odoo"
    config_dir = cfg.get("config_dir")
    if config_dir:
        run_env["CODEX_HOME"] = config_dir
    else:
        run_env.pop("CODEX_HOME", None)
    run_env.pop("OPENAI_API_KEY", None)
    run_env.pop("CODEX_API_KEY", None)
    return run_env


def check_login(cfg, timeout=30):
    """Run ``codex login status`` under the account's env; raise if not logged in.

    Exit code 0 means a usable login (verified on codex-cli 0.139: non-zero +
    'Not logged in' otherwise), making this a cheap, no-token liveness check.
    """
    binary = resolve_cli_binary(cfg.get("cli_path"))
    if not binary:
        raise UserError(_(
            "The Codex CLI was not found on this server. Install it (npm i -g "
            "@openai/codex), or set the account's 'CLI binary path' or the "
            "ERA_AI_CODEX_BIN environment variable."))
    proc = subprocess.run(
        [binary, "login", "status"],
        capture_output=True, text=True, timeout=timeout,
        env=_build_env(cfg),
        preexec_fn=_preexec_unlimit_as if resource else None,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise UserError(_(
            "The Codex CLI is installed but not signed in for this account (%s). "
            "Connect a ChatGPT account on the AI account form, or run "
            "'codex login' on the server.", detail or "not logged in"))
    return True


def cli_complete(cfg, model, system_prompt, user_prompt, timeout=180):
    """Run a single chat completion through ``codex exec`` and return the text.

    ``cfg`` is the plain dict built by ``era.ai.account._cli_cfg()`` (no ORM
    coupling) — same contract as the Claude transport's ``cli_complete``.
    """
    binary = resolve_cli_binary(cfg.get("cli_path"))
    if not binary:
        raise UserError(_(
            "The Codex CLI was not found on this server. Install it (npm i -g "
            "@openai/codex), or set the account's 'CLI binary path' or the "
            "ERA_AI_CODEX_BIN environment variable."))

    # Pure text generation, nothing else: read-only sandbox (the exec default,
    # restated defensively), no shell tool, no web search, no user config.toml
    # (auth still resolves via CODEX_HOME), no session files. exec never asks
    # for approval — denied actions just fail back to the model.
    args = [
        binary, "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox", "read-only",
        "--disable", "shell_tool",
        "-c", 'web_search="disabled"',
        "--ignore-user-config",
        "--color", "never",
        "--json",
    ]
    if model:
        args += ["--model", model]
    if cfg.get("extra_args"):
        args += shlex.split(cfg["extra_args"])
    args += ["-"]  # read the prompt from stdin

    # codex exec has no system-prompt flag; fold the system text into the stdin
    # document as a clearly delimited instructions block ahead of the user turn.
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
    slots = int(cfg.get("concurrency", 1) or 1)
    home = cfg.get("home_dir") or "/opt/odoo"

    with _global_slot(slots, lock_wait, lock_name=_LOCK_SLOT):
        _enforce_gap(gap, state_file=_STATE_FILE)
        try:
            _logger.info(
                "era_ai_accounts: invoking Codex CLI %s (model=%s, req=%dB, gap=%.1fs)",
                binary, model or "default", req_size, gap)
            proc = subprocess.run(
                args,
                input=stdin_doc,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_build_env(cfg),
                cwd=home,
                check=False,
                # The npm-distributed codex is a Node wrapper around the Rust
                # binary; lift the worker's soft RLIMIT_AS for it like for Claude.
                preexec_fn=_preexec_unlimit_as if resource else None,
            )
        except subprocess.TimeoutExpired:
            raise UserError(_("The AI request timed out after %ss.", timeout))
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UserError(_("Failed to launch the Codex CLI: %s", exc))
        finally:
            _record_call_end(state_file=_STATE_FILE)

    return _parse_codex_jsonl(proc.stdout, proc.returncode, proc.stderr)


def _parse_codex_jsonl(stdout, returncode, stderr):
    """Extract the final agent message from a ``codex exec --json`` event stream.

    The stream is JSONL: thread.started / turn.started / item.completed /
    turn.completed, with errors as {"type":"error","message":...} or a
    turn.failed event. Codex sometimes exits 0 on error paths (observed on
    0.139), so the events are authoritative — the exit code is only a fallback
    signal when no parsable events arrived.
    """
    raw = (stdout or "").strip()
    last_message = ""
    errors = []
    saw_event = False
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        saw_event = True
        etype = event.get("type") or ""
        if etype == "error":
            errors.append(str(event.get("message") or "unknown error"))
        elif etype == "turn.failed":
            err = event.get("error")
            if isinstance(err, dict):
                err = err.get("message") or err
            errors.append(str(err or "turn failed"))
        elif etype == "item.completed":
            item = event.get("item") or {}
            if not isinstance(item, dict):
                continue
            itype = item.get("type") or item.get("item_type") or ""
            if itype in ("agent_message", "assistant_message"):
                text = item.get("text") or item.get("message") or ""
                if isinstance(text, list):  # content-block shape, just in case
                    text = "".join(
                        b.get("text", "") for b in text if isinstance(b, dict))
                if isinstance(text, str) and text.strip():
                    last_message = text

    if last_message:
        return last_message
    if errors:
        detail = "; ".join(errors)[:500]
        _logger.warning("era_ai_accounts: Codex CLI error events: %s", detail)
        raise UserError(_("Codex CLI error: %s", detail))
    if not saw_event and returncode == 0 and raw:
        # Defensive: a future version printing plain text is still an answer.
        return raw
    detail = ((stderr or "").strip() or raw)[:500]
    _logger.warning(
        "era_ai_accounts: Codex CLI gave no answer (exit %s): %s", returncode, detail)
    if returncode != 0:
        raise UserError(_("Codex CLI error: %s", detail or "unknown error"))
    raise UserError(_("The Codex CLI returned an empty response."))
