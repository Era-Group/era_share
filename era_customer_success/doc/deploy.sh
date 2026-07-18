#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Safe validate -> deploy helper for era_customer_success on the ERA host.
#
# Why: the live prod Odoo shares this 64 GB host. A full fresh `-i` install
# (or several concurrent odoo-bin processes) can OOM the live server. This
# script ONLY ever runs ONE lightweight `-u <module> --stop-after-init`
# (workers auto-reload via registry signaling) and validates on the sandbox
# first. It NEVER runs `-i`.
#
# Usage:
#   ./deploy.sh            # validate on sandbox, then deploy to prod
#   ./deploy.sh --check    # validate on sandbox ONLY (no prod deploy)
#   ./deploy.sh --i18n     # validate + deploy WITH --i18n-overwrite (use when ar.po changed)
#   ./deploy.sh --prod-only# skip sandbox, deploy to prod directly (use sparingly)
# ---------------------------------------------------------------------------
set -euo pipefail

# ---- config (edit if paths change) ----------------------------------------
PY=/opt/odoo/venv/bin/python
ODOO=/opt/odoo/ce/odoo-bin
CONF=/opt/odoo/odoo.conf
MODULE=era_customer_success
MOD_DIR=/opt/odoo/addons/era_customer_success
SANDBOX_DB=test_cs_validate
PROD_DB=ae3229b2-5291-4967-80e1-6368dfecfaae
PROD_LOG=/var/log/odoo/odoo.log
MIN_FREE_MB=3000        # refuse to touch prod if available memory is below this

I18N=""; DO_SANDBOX=1; DO_PROD=1
for a in "$@"; do case "$a" in
  --i18n)      I18N="--i18n-overwrite" ;;
  --check)     DO_PROD=0 ;;
  --prod-only) DO_SANDBOX=0 ;;
  *) echo "unknown arg: $a"; exit 2 ;;
esac; done

say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die(){ printf '\033[1;31mABORT: %s\033[0m\n' "$*" >&2; exit 1; }
ERR_RE='ParseError|CRITICAL|Traceback|does not exist|no access rule|Wrong value|MemoryError|invalid view'
NOISE_RE='Missing.*translation|Deprecat|must have.*title|without-demo'

# ---- 0. guards ------------------------------------------------------------
say "guard: no other odoo-bin CLI / heavy install running"
if pgrep -fa "odoo-bin .*(-i |--init |-u .*-i )" | grep -v "$0" >/dev/null 2>&1; then
  die "an odoo-bin install/upgrade is already running — wait for it to finish"
fi

# ---- 1. static checks -----------------------------------------------------
say "static: py_compile + XML lint"
cd "$MOD_DIR"
$PY -m py_compile $(find . -name '*.py') && echo "  PY_OK"
$PY - <<PYEOF
from lxml import etree; import glob, sys
bad=0
for f in sorted(glob.glob('**/*.xml',recursive=True)):
    try: etree.parse(f)
    except Exception as e: bad=1; print("  XML FAIL", f, e)
print("  XML_OK" if not bad else "  XML_BAD"); sys.exit(1 if bad else 0)
PYEOF

# ---- 2. validate on sandbox ----------------------------------------------
if [ "$DO_SANDBOX" = 1 ]; then
  say "validate: -u $MODULE on $SANDBOX_DB (sandbox)"
  LOG=$(mktemp)
  $PY $ODOO -c "$CONF" -d "$SANDBOX_DB" -u "$MODULE" --stop-after-init --no-http \
       --log-level=warn --logfile="$LOG" >/dev/null 2>&1 || true
  if grep -iE "$ERR_RE" "$LOG" | grep -viE "$NOISE_RE" | head; then
    die "sandbox upgrade reported errors (see above) — fix before prod"
  fi
  echo "  sandbox upgrade clean ✓"; rm -f "$LOG"
fi

[ "$DO_PROD" = 0 ] && { say "done (--check): validated, NOT deployed"; exit 0; }

# ---- 3. memory guard ------------------------------------------------------
say "guard: host memory before touching prod"
FREE=$(free -m | awk 'NR==2{print $7}')
echo "  available: ${FREE} MB (need >= ${MIN_FREE_MB})"
[ "$FREE" -lt "$MIN_FREE_MB" ] && die "low memory — do not deploy now (risk of OOM on the live server)"

# ---- 4. deploy to prod ----------------------------------------------------
say "deploy: -u $MODULE on PROD ${I18N:+(+i18n)}"
POUT=$(mktemp)
$PY $ODOO -c "$CONF" -d "$PROD_DB" -u "$MODULE" $I18N --stop-after-init --no-http \
     --log-level=warn --logfile=/tmp/cs_prod_deploy.log >"$POUT" 2>&1 \
  && echo "  prod upgrade exit 0 ✓" || die "prod upgrade FAILED (see /tmp/cs_prod_deploy.log)"
grep -iE "$ERR_RE" /tmp/cs_prod_deploy.log | grep -viE "$NOISE_RE" | head && die "errors during prod upgrade" || true

# ---- 5. health + version --------------------------------------------------
say "verify: version + health"
PORT=$(grep -iE '^http_port' "$CONF" | grep -oE '[0-9]+' | head -1); PORT=${PORT:-8069}
VER=$(psql -d "$PROD_DB" -tAc "SELECT latest_version FROM ir_module_module WHERE name='$MODULE';" 2>/dev/null)
HTTP=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/web/health" || echo "ERR")
echo "  prod version: $VER | /web/health: $HTTP"
[ "$HTTP" = 200 ] && echo "  ✅ prod healthy" || echo "  ⚠️ health != 200 — check $PROD_LOG"

# ---- reminder: noupdate data needs a manual live write --------------------
say "reminder"
echo "  If you changed an ai.agent system_prompt or an ir.cron schedule (noupdate=1 data),"
echo "  the live record was NOT updated by this -u. Update it directly, e.g.:"
echo "    odoo-bin shell -c $CONF -d $PROD_DB --no-http"
echo "    >>> env.ref('era_customer_success.cs_<x>_agent').system_prompt = '''...'''; env.cr.commit()"
