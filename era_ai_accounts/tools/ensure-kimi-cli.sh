#!/usr/bin/env bash
# Make sure the Kimi Code CLI is installed where Odoo can find it.
# Idempotent and safe to call on every boot.
#
# Put EXACTLY this one line in the cicdoo startup script (it runs as root):
#
#     bash /opt/odoo/submodules/era_share_latest/era_ai_accounts/tools/ensure-kimi-cli.sh
#
# It lives in the repo on purpose: cicdoo clones the submodules BEFORE running
# the startup script (verified in the boot log), so every new server gets this
# file automatically and the startup line never has to change. Do not move it
# to a per-instance volume — that would need hand-placing on each server.
#
# Why this file exists: the installer puts the binary in "$HOME/.kimi-code",
# so running it as root lands it in /root/.kimi-code — a directory the odoo
# user cannot even read, so Odoo never finds it. That is exactly what happened
# on 2026-08-14 ("Installed to /root/.kimi-code/bin/kimi", then
# "/opt/odoo/.kimi-code/bin/kimi: No such file or directory"). This script
# re-executes itself as odoo so the install always lands in /opt/odoo.
set -uo pipefail

ODOO_USER=odoo
ODOO_HOME=/opt/odoo
TARGET="$ODOO_HOME/.kimi-code/bin/kimi"

log() { echo "[ensure-kimi] $*"; }

# --- running as root: clean up root's stray copy, then drop to odoo ----------
if [ "$(id -u)" -eq 0 ]; then
    if [ -d /root/.kimi-code ]; then
        log "removing the stray root-owned install ($(du -sh /root/.kimi-code 2>/dev/null | cut -f1)) — Odoo cannot read it"
        rm -rf /root/.kimi-code
    fi
    # `su -` gives a login shell with HOME=/opt/odoo, which is what decides
    # where the installer writes. (sudo/runuser are not present in this image.)
    exec su - "$ODOO_USER" -c "bash '$0' --as-odoo"
fi

# --- running as odoo --------------------------------------------------------
if [ "$(id -un)" != "$ODOO_USER" ]; then
    log "WARNING: running as $(id -un), not $ODOO_USER — the install may land outside $ODOO_HOME"
fi
log "HOME=$HOME"

if [ -x "$TARGET" ] && "$TARGET" --version >/dev/null 2>&1; then
    log "already installed: $("$TARGET" --version 2>&1 | head -1)"
else
    log "installing the Kimi Code CLI ..."
    if ! curl -fsSL https://code.kimi.com/kimi-code/install.sh | KIMI_NO_MODIFY_PATH=1 bash; then
        log "ERROR: installer failed"
        exit 1
    fi
fi

# The installer keeps the previous binary as a 172 MB .bak on every upgrade.
rm -f "$ODOO_HOME/.kimi-code/bin/kimi.bak"

# The real check: a too-old glibc installs fine and only fails at run time.
if ! version=$("$TARGET" --version 2>&1 | head -1); then
    log "ERROR: $TARGET is present but will not run: $version"
    exit 1
fi
log "ready: $TARGET ($version)"
