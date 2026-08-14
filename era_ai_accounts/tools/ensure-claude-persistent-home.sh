#!/usr/bin/env bash
# Keep Claude Code's state on the persistent volume, so a container rebuild
# stops destroying transcripts, memory and the login.
#
# Add this line to the cicdoo startup script, next to ensure-kimi-cli.sh:
#
#     bash /opt/odoo/submodules/era_share_latest/era_ai_accounts/tools/ensure-claude-persistent-home.sh
#
# What it does: makes /opt/odoo/.claude a SYMLINK to /var/lib/odoo/claude-home
# (migrating whatever is already there on the first run), and exports
# CLAUDE_CONFIG_DIR in ~/.bashrc.
#
# Why both:
#   * The Claude Code VS Code extension spawns the CLI *directly* from the
#     extension host — not through a shell — so it never reads ~/.bashrc and
#     an env var cannot reach it. Only the symlink covers that path.
#   * A shell-launched `claude` does read ~/.bashrc, and CLAUDE_CONFIG_DIR is
#     the more complete fix: verified on 2.1.232 that with it set, EVERYTHING
#     (including ~/.claude.json) is written inside that directory and nothing
#     lands in $HOME. Both point at the same directory, so they cannot diverge.
#
# Remaining gap: launched from the extension without the env var, Claude still
# writes ~/.claude.json (account + MCP connectors) to $HOME, which is on the
# wiped overlay. That single file is handled by the snapshot/restore scripts in
# /var/lib/odoo/claude-persist/.
#
# SAFE TO RUN AT BOOT. It refuses to migrate while Claude is running, so it
# never pulls the directory out from under a live session.
set -uo pipefail

ODOO_USER=odoo
ODOO_HOME=/opt/odoo
LIVE="$ODOO_HOME/.claude"
PERSIST=/var/lib/odoo/claude-home
BASHRC="$ODOO_HOME/.bashrc"
EXPORT_LINE="export CLAUDE_CONFIG_DIR=$PERSIST"

log() { echo "[claude-home] $*"; }

# --- as root: drop to odoo so everything is created with the right owner -----
if [ "$(id -u)" -eq 0 ]; then
    exec su - "$ODOO_USER" -c "bash '$0' --as-odoo"
fi

mkdir -p "$PERSIST" || { log "ERROR: cannot create $PERSIST"; exit 1; }
chmod 700 "$PERSIST" 2>/dev/null || true

# --- 1. make $HOME/.claude point at the volume -------------------------------
if [ -L "$LIVE" ]; then
    target=$(readlink -f "$LIVE")
    if [ "$target" = "$(readlink -f "$PERSIST")" ]; then
        log "already linked: $LIVE -> $PERSIST"
    else
        log "WARNING: $LIVE is a symlink to $target, not $PERSIST — leaving it alone"
    fi
elif [ -d "$LIVE" ]; then
    # Never yank the directory away from a running session.
    if pgrep -f "native-binary/claude" >/dev/null 2>&1; then
        log "Claude is running — not migrating now. Re-run this when it is closed"
        log "(at boot nothing is running, so the startup script will do it)."
    else
        log "migrating $LIVE ($(du -sh "$LIVE" 2>/dev/null | cut -f1)) into $PERSIST ..."
        # Merge without deleting: an existing volume copy wins nothing, but no
        # file is lost either way.
        if cp -an "$LIVE/." "$PERSIST/" 2>/dev/null; then
            mv "$LIVE" "$LIVE.migrated.$(date +%s)" &&
            ln -s "$PERSIST" "$LIVE" &&
            log "linked: $LIVE -> $PERSIST (old dir kept as $LIVE.migrated.*)"
        else
            log "ERROR: copy failed — left everything as it was"
        fi
    fi
else
    ln -s "$PERSIST" "$LIVE" && log "linked: $LIVE -> $PERSIST (fresh)"
fi

# --- 2. env var for shell-launched claude (the more complete fix) ------------
if [ -f "$BASHRC" ] && grep -qF "CLAUDE_CONFIG_DIR" "$BASHRC"; then
    log "CLAUDE_CONFIG_DIR already in $BASHRC"
else
    printf '\n# Keep Claude Code state on the persistent volume (survives rebuilds).\n%s\n' \
        "$EXPORT_LINE" >> "$BASHRC" && log "added CLAUDE_CONFIG_DIR to $BASHRC"
fi

# --- 2b. ~/.claude.json ------------------------------------------------------
# It lives BESIDE the directory, not inside it, and Claude rewrites it with
# temp+rename — which replaces a symlink with a regular file, so linking it is
# not an option. Keep a copy on the volume instead: seed it when missing (right
# after a rebuild) and refresh it whenever the live file is present.
JSON_LIVE="$ODOO_HOME/.claude.json"
JSON_BAK="$PERSIST/.claude-json.backup"
if [ -f "$JSON_LIVE" ]; then
    cp -a "$JSON_LIVE" "$JSON_BAK" 2>/dev/null && log "backed up .claude.json ($(du -h "$JSON_BAK" | cut -f1))"
elif [ -f "$JSON_BAK" ]; then
    cp -a "$JSON_BAK" "$JSON_LIVE" && chmod 600 "$JSON_LIVE" &&
        log "restored .claude.json from the volume (account + MCP connectors)"
fi

# --- 3. report --------------------------------------------------------------
if [ -d "$PERSIST/projects/-opt-odoo" ]; then
    log "state: $(find "$PERSIST/projects/-opt-odoo" -maxdepth 1 -name '*.jsonl' | wc -l) transcript(s), $(ls -1 "$PERSIST/projects/-opt-odoo/memory" 2>/dev/null | wc -l) memory file(s)"
fi
log "ready: $PERSIST ($(du -sh "$PERSIST" 2>/dev/null | cut -f1))"
