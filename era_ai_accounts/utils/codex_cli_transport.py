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

Codex tokens rotate on refresh and ``auth.json`` is single-writer, so this
transport HARD-CLAMPS its slot pool to 1: at most one ``codex`` process per
host, regardless of ``ai.cli_max_concurrency`` (which still sizes the Claude
pool). Two concurrent refreshes would otherwise race last-writer-wins and
persist an already-consumed refresh token, breaking the linked account for
everyone until it is re-linked.
"""
import glob
import json
import logging
import os
import re
import shlex
import shutil
import signal
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
    Takes the same host-wide slot as cli_complete: even a status probe may
    refresh-and-rewrite auth.json, which is single-writer.
    """
    binary = resolve_cli_binary(cfg.get("cli_path"))
    if not binary:
        raise UserError(_(
            "The Codex CLI was not found on this server. Install it (npm i -g "
            "@openai/codex), or set the account's 'CLI binary path' or the "
            "ERA_AI_CODEX_BIN environment variable."))
    with _global_slot(1, min(float(cfg.get("lock_wait", 300.0)), 30.0),
                      lock_name=_LOCK_SLOT):
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


# ---------------------------------------------------------- device-code login
# `codex login --device-auth` prints a verification URL + one-time code, then
# waits (~15 min) for the user to approve from any browser and writes auth.json
# itself. We spawn it detached, stream its output to a file in the managed
# config dir, and parse the URL/code out of that file for the wizard.
_DEVICE_OUT_FILE = "device_login.out"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DEVICE_URL_RE = re.compile(r"https://[^\s'\"<>]+")
# One-time codes are short grouped tokens (e.g. "BLDQ-NRVF") or plain digits.
_DEVICE_CODE_RE = re.compile(r"\b([A-Z0-9]{3,6}-[A-Z0-9]{3,6}|\d{6,9})\b")


def device_login_start(cfg):
    """Spawn ``codex login --device-auth`` for this account; return (pid, out_path).

    ``cfg`` must carry a managed ``config_dir`` (the link target). The child is
    detached into its own session so an Odoo worker recycle does not kill the
    pending login; it exits by itself on approval, denial, or code expiry.
    """
    binary = resolve_cli_binary(cfg.get("cli_path"))
    if not binary:
        raise UserError(_(
            "The Codex CLI was not found on this server. Install it (npm i -g "
            "@openai/codex), or set the account's 'CLI binary path' or the "
            "ERA_AI_CODEX_BIN environment variable."))
    config_dir = cfg.get("config_dir")
    if not config_dir:
        raise UserError(_("Device login needs the account's managed credentials directory."))
    out_path = os.path.join(config_dir, _DEVICE_OUT_FILE)
    out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        proc = subprocess.Popen(
            [binary, "login", "--device-auth"],
            stdin=subprocess.DEVNULL, stdout=out_fd, stderr=subprocess.STDOUT,
            env=_build_env(cfg),
            cwd=cfg.get("home_dir") or "/opt/odoo",
            start_new_session=True,
            preexec_fn=_preexec_unlimit_as if resource else None,
        )
    except Exception as exc:  # noqa: BLE001
        raise UserError(_("Failed to launch the Codex CLI: %s", exc))
    finally:
        os.close(out_fd)
    _logger.info("era_ai_accounts: codex device login started (pid %s)", proc.pid)
    return proc.pid, out_path


def device_login_read(out_path):
    """Raw output of a pending device login, ANSI-escape-stripped."""
    try:
        with open(out_path, "r", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return ""
    return _ANSI_RE.sub("", raw)


def parse_device_login(raw):
    """Best-effort (url, code) from codex's device-auth output."""
    url = ""
    m = _DEVICE_URL_RE.search(raw or "")
    if m:
        url = m.group(0).rstrip(").,;:")
    # Don't let URL fragments (ports, ids) masquerade as the one-time code.
    scrubbed = (raw or "").replace(url, "")
    mc = _DEVICE_CODE_RE.search(scrubbed)
    code = mc.group(0) if mc else ""
    return url, code


def pid_is_pending_login(pid):
    """True while ``pid`` is alive and is a codex process (guards stale pids)."""
    if not pid:
        return False
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            return b"codex" in fh.read()
    except OSError:
        return False


def device_login_kill(pid):
    """Terminate a pending device login if (and only if) it is still codex."""
    if pid_is_pending_login(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


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
        try:
            args += shlex.split(cfg["extra_args"])
        except ValueError as exc:
            raise UserError(_(
                "Invalid 'CLI extra arguments' on this AI account: %s", exc))
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
    # auth.json is single-writer (tokens rotate on refresh): the Codex pool is
    # always 1 slot, deliberately ignoring ai.cli_max_concurrency — raising
    # that knob for Claude must not let two codex processes race a refresh.
    slots = 1
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
