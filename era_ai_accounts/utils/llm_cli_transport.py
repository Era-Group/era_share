"""Local CLI-proxy transport for AI accounts.

Routes a chat request through a first-party AI CLI that is already authenticated
on this server (today: the Claude Code ``claude`` binary). The CLI performs the
model call itself under its own auth, which is the legitimate way to use a
"connected account" — we never read or replay the CLI's credentials.

Only chat/text completion is supported (the standard Odoo tool-calling loop and
embeddings are not available through this transport).
"""
import glob
import json
import logging
import os
import shlex
import subprocess
import tempfile
import threading

try:
    import resource
except ImportError:  # pragma: no cover - non-unix
    resource = None

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Default search globs for the Claude Code native CLI shipped with the VS Code /
# code-server extension. Newest version directory wins.
_CLAUDE_GLOBS = [
    "/opt/odoo/.local/share/code-server/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    os.path.expanduser("~/.local/share/code-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"),
    os.path.expanduser("~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"),
]

# Per-(process, account) concurrency gates. Best-effort: Odoo runs several worker
# processes, so this caps concurrency within a worker, not across the whole host.
_semaphores = {}
_semaphores_lock = threading.Lock()


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


def _gate(account_id, max_concurrency):
    max_concurrency = max(1, int(max_concurrency or 1))
    with _semaphores_lock:
        sem = _semaphores.get(account_id)
        if sem is None or getattr(sem, "_era_size", None) != max_concurrency:
            sem = threading.BoundedSemaphore(max_concurrency)
            sem._era_size = max_concurrency
            _semaphores[account_id] = sem
        return sem


def cli_complete(cfg, model, system_prompt, user_prompt, timeout=180):
    """Run a single chat completion through the local CLI and return the text.

    ``cfg`` is a plain dict (no ORM coupling):
        {account_id, cli_path, home_dir, extra_args, max_concurrency}
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
    # Disable all built-in tools so this behaves as a pure chat completion.
    args += ["--allowed-tools", ""]
    if cfg.get("extra_args"):
        args += shlex.split(cfg["extra_args"])

    run_env = dict(os.environ)
    home = cfg.get("home_dir") or "/opt/odoo"
    run_env["HOME"] = home
    # Never inherit an API key into the subprocess: we want it to use the
    # connected account's own (subscription/OAuth) auth, not bill an API key.
    run_env.pop("ANTHROPIC_API_KEY", None)

    sem = _gate(cfg.get("account_id") or 0, cfg.get("max_concurrency", 2))
    if not sem.acquire(timeout=max(1, int(timeout))):
        raise UserError(_("The AI account is busy (max concurrency reached). Please retry."))
    sysfile = None
    try:
        # The system prompt can be very large (RAG context, model/menu listings),
        # so pass it via a file — putting it in argv overflows the OS argument
        # limit (E2BIG / "argument list too long"). The user prompt goes on stdin.
        if system_prompt:
            fd, sysfile = tempfile.mkstemp(prefix="era_ai_sys_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(system_prompt)
            args += ["--append-system-prompt-file", sysfile]

        _logger.info("era_ai_accounts: invoking CLI %s (model=%s)", binary, model or "default")
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
        try:
            sem.release()
        except (ValueError, threading.ThreadError):
            pass
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
