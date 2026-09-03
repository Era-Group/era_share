#!/usr/bin/env bash
# Bring this container's Tour Assistant up to date, on purpose rather than by
# accident.
#
# Three things have to happen together, and doing two of them is worse than
# doing none: the code has to arrive, the server has to load it, and the
# walkthroughs an older builder wrote have to go. Skip the third and the next
# person to try the module walks exactly the fault that was just fixed — which
# is how one container sat three builds behind while every screen said it was
# fine.
#
#     bash tools/update.sh                 # pull, restart, drop what is stale
#     bash tools/update.sh --rebuild       # and answer those questions again now
#
# The rebuild is optional because it costs one model call per question. Without
# it the walkthroughs are simply rebuilt as people ask for them again.

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REBUILD=false
[ "${1:-}" = "--rebuild" ] && REBUILD=true

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- the code
say "1/3  The code"
before=$(git -C "$HERE" rev-parse --short HEAD)
git -C "$HERE" pull --ff-only
after=$(git -C "$HERE" rev-parse --short HEAD)
if [ "$before" = "$after" ]; then
    echo "  already at $after"
else
    echo "  $before -> $after"
    git -C "$HERE" log --oneline "$before..$after" | sed 's/^/    /'
fi

# -------------------------------------------------------------- the server
say "2/3  The server"
# Loading the new python is a restart, not a reload: the builder is imported
# once at start, and a server that was not restarted answers with the old one
# while the files on disk say otherwise.
if command -v cicdoo >/dev/null 2>&1; then
    cicdoo restart "$(basename "$HERE")"
elif [ -n "${ODOO_BIN:-}" ]; then
    echo "  cicdoo not here — upgrade with:"
    echo "    $ODOO_BIN -c \$CONF -d \$DB -u $(basename "$HERE") --stop-after-init"
    echo "  then restart the server."
else
    echo "  No cicdoo on this container. Upgrade the module and restart the"
    echo "  server however this instance is run, then re-run this script."
    exit 1
fi

# -------------------------------------------------------- the walkthroughs
say "3/3  The walkthroughs"
DB=${DB:-$(grep -oP '(?<=^dbfilter\s=\s\^).*(?=\$$)' /opt/odoo/odoo.conf 2>/dev/null || true)}
if [ -z "$DB" ]; then
    echo "  Which database? Set DB=… and run again; nothing was dropped."
    exit 1
fi

PYTHON=${PYTHON:-/opt/odoo/venv/bin/python}
ODOO=${ODOO:-/opt/odoo/ce/odoo-bin}
CONF=${CONF:-/opt/odoo/odoo.conf}

REBUILD=$REBUILD "$PYTHON" "$ODOO" shell -c "$CONF" -d "$DB" --no-http <<'PY'
import os

tours = env["web_tour.tour"].sudo()
requests = env["tour.assistant.request"].sudo()

dropped = tours._assistant_drop_stale()
reopened = tours._assistant_reopen_after_upgrade()
env.cr.commit()
print("  dropped %d written by an older builder, reopened %d given up on"
      % (dropped, reopened), flush=True)

if os.environ.get("REBUILD") != "true":
    print("  the questions behind them are queued; they rebuild as people ask",
          flush=True)
else:
    pending = requests.search(
        [("state", "in", ("queued", "matched")), ("tour_id", "=", False)],
        order="id")
    print("  rebuilding %d now" % len(pending), flush=True)
    admin = env.ref("base.user_admin")
    built = 0
    for record in pending:
        asker = record.user_ids[:1]
        if not asker or not asker.active:
            asker = admin
        # As the person who asked, and in their language: a walkthrough is
        # stored text, and which language it is stored in has to come from the
        # reader rather than from whatever ran the rebuild.
        scoped = env(user=asker.id, su=False,
                     context=dict(env.context, lang=asker.lang or "en_US"))
        try:
            answer = scoped["tour.assistant.request"].ask(record.name)
            env.cr.commit()
            built += bool((answer.get("tour") or {}).get("name"))
        except Exception as error:
            env.cr.rollback()
            print("    could not: %s (%s)" % (record.name[:40], str(error)[:50]),
                  flush=True)
    print("  built %d" % built, flush=True)
PY

say "Done. Settings › Tour Assistant now names the build it is running."
