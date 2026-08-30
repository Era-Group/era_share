#!/bin/bash
# Keep the local embeddings service alive.
#
# Why a supervisor at all: this container has no systemd and no supervisord,
# and a dead embedder fails *silently* — knowledge sources simply sit in
# "processing" forever with no error anywhere. So the loop below is the only
# thing standing between a crash and RAG quietly not working.
#
# Started from the container entrypoint with a single line:
#   setsid /var/lib/odoo/era_embed/start.sh >> /var/lib/odoo/era_embed/server.log 2>&1 &
#
# See era_share/era_ai_accounts/docs/LOCAL_EMBEDDINGS.md
set -u

# Derive the install root from this script's own location, so the same file
# works on an Odoo host and on a standalone embeddings server.
DIR="${ERA_EMBED_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PYTHON="$DIR/venv/bin/python"
SERVER="$DIR/server.py"
LOG="$DIR/server.log"
LOCK="$DIR/.start.lock"
MAX_LOG_BYTES=$((50 * 1024 * 1024))
RESTART_DELAY=10

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') start.sh: $*"; }

# One supervisor only. Without this a second entrypoint run would spawn a
# rival loop that restart-fights the first one.
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
    log "another supervisor already holds $LOCK — exiting"
    exit 0
fi

if [ ! -x "$PYTHON" ] || [ ! -f "$SERVER" ]; then
    log "not installed at $DIR — skipping; RAG indexing will be unavailable"
    exit 0
fi

# The log is appended to forever by a process nothing rotates.
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]; then
    tail -c $((MAX_LOG_BYTES / 5)) "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    log "log truncated"
fi

trap 'log "supervisor stopping"; kill 0; exit 0' TERM INT

log "supervising $SERVER"
while true; do
    "$PYTHON" "$SERVER"
    code=$?
    case $code in
        3) log "port already served by another instance — exiting"; exit 0 ;;
        1) log "could not bind the port — exiting"; exit 1 ;;
        0) log "server exited cleanly — exiting"; exit 0 ;;
        *) log "server died (exit $code) — restarting in ${RESTART_DELAY}s" ;;
    esac
    sleep "$RESTART_DELAY"
done
