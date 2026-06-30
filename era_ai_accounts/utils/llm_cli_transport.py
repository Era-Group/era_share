"""Local CLI-proxy transport for AI accounts.

Routes a chat request through a first-party AI CLI that is already authenticated
on this server (today: the Claude Code ``claude`` binary). The CLI performs the
model call itself under its own auth, which is the legitimate way to use a
"connected account" — we never read or replay the CLI's credentials.

To stay gentle on the connected account, calls are **globally serialized** (at
most one CLI call at a time across every Odoo worker / user, via a host-wide file
lock) and separated by a **gap proportional to the request body size** (bigger
requests wait longer). Both are tunable via ``ir.config_parameter``.

Only chat/text completion is supported (the standard Odoo tool-calling loop and
embeddings are not available through this transport).
"""
import contextlib
import glob
import json
import logging
import os
import shlex
import subprocess
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - non-unix
    fcntl = None
try:
    import resource
except ImportError:  # pragma: no cover - non-unix
    resource = None

from odoo import _
from odoo.exceptions import UserError
from odoo.tools import config

_logger = logging.getLogger(__name__)

# Default search globs for the Claude Code native CLI shipped with the VS Code /
# code-server extension. Newest version directory wins.
_CLAUDE_GLOBS = [
    "/opt/odoo/.local/share/code-server/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    os.path.expanduser("~/.local/share/code-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"),
    os.path.expanduser("~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"),
]

# Host-wide serialization. The file locks work across worker processes; the
# thread lock is only a fallback when fcntl is unavailable (non-unix).
_LOCK_SLOT = "era_ai_cli_proxy.%d.lock"
_STATE_FILE = "era_ai_cli_proxy.last"
_thread_lock = threading.Lock()


def _shared_dir():
    """A directory shared by every Odoo worker on this host (the data dir)."""
    return config.get("data_dir") or tempfile.gettempdir()


def _preexec_unlimit_as():  # pragma: no cover - runs in the forked child
    """Lift the inherited virtual-memory cap for the CLI child only.

    Odoo applies a *soft* RLIMIT_AS (= limit_memory_hard) to its workers; the
    hard limit stays unlimited. The Claude CLI's JS runtime (Bun/JavaScriptCore)
    reserves far more virtual address space than that soft cap and aborts with a
    'MemoryExhaustion' assertion. Raising the soft limit back to the hard limit
    in the child fixes it without touching the Odoo worker's own limit. Keep this
    minimal — it runs between fork() and exec() in a possibly-threaded process.
    """
    if resource is None:
        return
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
    except Exception:
        pass


def resolve_cli_binary(override=None):
    """Return the path to the AI CLI binary, or None if not found.

    Resolution order: explicit ``override`` -> ``$ERA_AI_CLAUDE_BIN`` -> newest
    Claude Code extension binary on disk.
    """
    if override and os.path.exists(override):
        return override
    env_bin = os.getenv("ERA_AI_CLAUDE_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    candidates = []
    for pattern in _CLAUDE_GLOBS:
        candidates.extend(glob.glob(pattern))
    if not candidates:
        return None
    # Sort by the version embedded in the path (…claude-code-<ver>-linux-x64/…);
    # mtime is a reliable tiebreaker for freshly installed updates.
    candidates.sort(key=lambda p: (os.path.getmtime(p), p))
    return candidates[-1]


@contextlib.contextmanager
def _global_slot(slots, wait_seconds):
    """Hold one of ``slots`` host-wide slots for the duration of one CLI call.

    A cross-process counting semaphore built from ``slots`` lock files: a call
    grabs the first free slot, so at most ``slots`` Claude CLI calls run at once
    across *all* Odoo workers and users (``slots=1`` => strictly one at a time).
    The OS releases a slot automatically if a worker dies, so a crash can't
    deadlock the queue. Falls back to a per-process thread lock when fcntl is
    unavailable (non-unix).
    """
    slots = max(1, int(slots or 1))
    if fcntl is None:
        with _thread_lock:
            yield
        return
    shared = _shared_dir()
    fds = [os.open(os.path.join(shared, _LOCK_SLOT % i), os.O_CREAT | os.O_RDWR, 0o600)
           for i in range(slots)]
    held = None
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    try:
        while held is None:
            for fd in fds:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = fd
                    break
                except OSError:
                    continue
            if held is None:
                if time.monotonic() >= deadline:
                    raise UserError(_(
                        "The AI service is busy (all %s slot(s) in use). Please retry in a moment.",
                        slots))
                time.sleep(0.2)
        yield
    finally:
        if held is not None:
            try:
                fcntl.flock(held, fcntl.LOCK_UN)
            except OSError:
                pass
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


def _compute_gap(cfg, req_size):
    """Seconds to keep between consecutive calls, scaled by request body size."""
    min_gap = float(cfg.get("min_gap", 1.0))
    per_kb = float(cfg.get("gap_per_kb", 0.05))
    max_gap = float(cfg.get("max_gap", 30.0))
    return max(0.0, min(max_gap, min_gap + per_kb * (req_size / 1024.0)))


def _enforce_gap(gap):
    """Sleep so that at least ``gap`` seconds have elapsed since the last call ended.

    The last-call time is shared across workers via a small state file, so the
    spacing is global, not per-process. Must be called while holding the lock.
    """
    if gap <= 0:
        return
    last = 0.0
    try:
        with open(os.path.join(_shared_dir(), _STATE_FILE)) as fh:
            last = float(fh.read().strip() or 0)
    except (OSError, ValueError):
        last = 0.0
    wait = gap - (time.time() - last)
    if wait > 0:
        time.sleep(min(wait, gap))


def _record_call_end():
    try:
        with open(os.path.join(_shared_dir(), _STATE_FILE), "w") as fh:
            fh.write(repr(time.time()))
    except OSError:
        pass


def cli_complete(cfg, model, system_prompt, user_prompt, timeout=180,
                 allowed_tools=""):
    """Run a single chat completion through the local CLI and return the text.

    ``cfg`` is a plain dict (no ORM coupling):
        {account_id, cli_path, home_dir, extra_args,
         min_gap, gap_per_kb, max_gap, lock_wait}

    ``allowed_tools`` is the value handed to the CLI ``--allowed-tools`` flag.
    It DEFAULTS TO ``""`` (empty) so the CLI behaves as a pure chat completion
    with every built-in tool disabled — the historical, all-agents behaviour. A
    caller that explicitly opts in (e.g. ``"WebSearch"``) gets exactly that tool
    whitelisted in this single headless ``-p`` run; in ``-p`` mode the allow-list
    is the programmatic grant (no interactive prompt is possible).
    """
    binary = resolve_cli_binary(cfg.get("cli_path"))
    if not binary:
        raise UserError(_(
            "The Claude CLI was not found on this server. Set the account's "
            "'CLI binary path' or the ERA_AI_CLAUDE_BIN environment variable."
        ))

    args = [binary, "-p", "--output-format", "json"]
    if model:
        args += ["--model", model]
    # Built-in tools are disabled by default (allowed_tools=""), so this behaves
    # as a pure chat completion. A caller may opt a tool in per-call.
    args += ["--allowed-tools", allowed_tools or ""]
    if cfg.get("extra_args"):
        args += shlex.split(cfg["extra_args"])

    run_env = dict(os.environ)
    home = cfg.get("home_dir") or "/opt/odoo"
    run_env["HOME"] = home
    # Never inherit an API key into the subprocess: we want it to use the
    # connected account's own (subscription/OAuth) auth, not bill an API key.
    run_env.pop("ANTHROPIC_API_KEY", None)

    req_size = len(system_prompt or "") + len(user_prompt or "")
    gap = _compute_gap(cfg, req_size) if cfg.get("gap_enabled", True) else 0.0
    lock_wait = float(cfg.get("lock_wait", 300.0))
    slots = int(cfg.get("concurrency", 1) or 1)

    proc = None
    sysfile = None
    # At most `slots` CLI calls at a time across the whole host (default 1), with a
    # size-proportional gap between calls, so the connected Claude account is never
    # hit by concurrent or rapid-fire requests. Both are set in Settings > AI.
    with _global_slot(slots, lock_wait):
        _enforce_gap(gap)
        try:
            # The system prompt can be very large (RAG context, model/menu
            # listings), so pass it via a file — putting it in argv overflows the
            # OS argument limit (E2BIG). The user prompt goes on stdin.
            if system_prompt:
                fd, sysfile = tempfile.mkstemp(prefix="era_ai_sys_", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(system_prompt)
                args += ["--append-system-prompt-file", sysfile]

            _logger.info(
                "era_ai_accounts: invoking CLI %s (model=%s, req=%dB, gap=%.1fs)",
                binary, model or "default", req_size, gap)
            proc = subprocess.run(
                args,
                input=(user_prompt or ""),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                cwd=home,
                check=False,
                preexec_fn=_preexec_unlimit_as if resource else None,
            )
        except subprocess.TimeoutExpired:
            raise UserError(_("The AI request timed out after %ss.", timeout))
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UserError(_("Failed to launch the AI CLI: %s", exc))
        finally:
            # Stamp the end time even on failure, so the gap also spaces out
            # retries after an error/timeout.
            _record_call_end()
            if sysfile:
                try:
                    os.unlink(sysfile)
                except OSError:
                    pass

    if proc.returncode != 0:
        # The CLI often returns a structured error on stdout even on non-zero exit;
        # surface its clean message rather than dumping the raw JSON to the user.
        detail = _extract_cli_error(proc.stdout) or (proc.stderr or "").strip()
        _logger.warning("era_ai_accounts: CLI exited %s: %s", proc.returncode, detail[:500])
        raise UserError(_("AI CLI error: %s", detail[:500] or "unknown error"))

    return _parse_cli_json(proc.stdout)


def _extract_cli_error(stdout):
    """Pull a human-readable message out of a CLI JSON error payload, if present."""
    raw = (stdout or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:500]
    if isinstance(data, dict):
        msg = data.get("result") or data.get("error") or ""
        if isinstance(msg, dict):
            msg = msg.get("message") or ""
        return str(msg)
    return ""


def _parse_cli_json(stdout):
    raw = (stdout or "").strip()
    if not raw:
        raise UserError(_("The AI CLI returned an empty response."))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Some versions may print plain text in -p mode; accept it as the answer.
        return raw
    if isinstance(data, dict):
        if data.get("is_error"):
            raise UserError(_("AI CLI error: %s", data.get("result") or data.get("error") or "unknown"))
        text = data.get("result")
        if isinstance(text, str) and text.strip():
            return text
        # stream-json / message shapes: dig for text blocks.
        message = data.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if any(parts):
                return "".join(parts)
    raise UserError(_("Could not parse the AI CLI response."))
