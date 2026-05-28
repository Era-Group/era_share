#!/usr/bin/env bash
#
# verify_seo.sh — one-shot upgrade + test run for the ERA SEO suite.
#
# Runs an upgrade of the four SEO modules (which exercises the migrations
# and view loading) WITH the test suite enabled, captures the log, and
# prints a clear PASS / FAIL verdict by scanning for Odoo's failure markers.
#
# Safe on a staging / neutralized DB: tests run inside transactions that are
# rolled back. The `-u` DOES run migrations (a real, intended change to the
# staging DB). Do not point this at production.
#
# Usage (Odoo.sh shell, or any host with odoo-bin on PATH):
#     bash verify_seo.sh                 # auto-detect DB
#     DB=mydb bash verify_seo.sh         # explicit DB
#     ODOO_BIN=odoo bash verify_seo.sh   # if the binary is `odoo`
#
set -uo pipefail

MODULES="era_seo_manager,era_seo_blog,era_seo_ai,era_seo_blog_ai,era_geo,era_geo_ai"
# `/module` selects every test in that module regardless of its @tagged value.
TEST_TAGS="/era_seo_manager,/era_seo_blog,/era_seo_ai,/era_seo_blog_ai,/era_geo,/era_geo_ai"
ODOO_BIN="${ODOO_BIN:-odoo-bin}"
ODOO_CONF="${ODOO_CONF:-}"
# HttpCase tests reach into odoo.service.server.server.httpd, so we can't
# use --no-http; pick a high port unlikely to collide with a running server.
HTTP_PORT="${HTTP_PORT:-18069}"
LOG="${LOG:-/tmp/era_seo_verify_$(date +%Y%m%d_%H%M%S).log}"

# Pick up the standard on-prem config if the caller didn't override it. Tests
# need the full addons_path; without -c, odoo-bin only sees the core paths and
# can't find era_* modules.
if [ -z "$ODOO_CONF" ] && [ -f /opt/odoo/odoo.conf ]; then
  ODOO_CONF="/opt/odoo/odoo.conf"
fi
CONF_FLAG=()
[ -n "$ODOO_CONF" ] && CONF_FLAG=(-c "$ODOO_CONF")

# --- Resolve the database name --------------------------------------------
if [ -z "${DB:-}" ]; then
  DB="${PGDATABASE:-}"
fi
if [ -z "${DB:-}" ]; then
  # Pick the single application DB (excludes postgres/templates).
  DB="$(psql -tAc \
    "SELECT datname FROM pg_database WHERE datname NOT IN ('postgres','template0','template1') ORDER BY datname LIMIT 1" \
    2>/dev/null | tr -d '[:space:]')"
fi
if [ -z "${DB:-}" ]; then
  echo "!! Could not detect the database name. Re-run as:  DB=<your_db> bash verify_seo.sh"
  exit 2
fi

echo "================================================================"
echo " ERA SEO verification"
echo "   odoo-bin : $ODOO_BIN"
echo "   database : $DB"
echo "   modules  : $MODULES"
echo "   log file : $LOG"
echo "================================================================"

# --- Run the upgrade + tests ----------------------------------------------
# --http-port avoids fighting a running server for port 8069. HttpCase tests
# need a live server (they introspect odoo.service.server.server.httpd), so
# we can't use --no-http.
# --logfile overrides any `logfile = …` in the loaded conf so output reaches
# this run's log instead of being silently appended to the server's log.
"$ODOO_BIN" "${CONF_FLAG[@]}" -d "$DB" \
  -u "$MODULES" \
  --test-enable \
  --test-tags "$TEST_TAGS" \
  --stop-after-init \
  --workers=0 \
  --http-port="$HTTP_PORT" \
  --logfile="$LOG" \
  --log-level=test
ODOO_EXIT=$?
# Mirror the captured log to stdout so callers see what happened live.
cat "$LOG"

# --- Verdict ---------------------------------------------------------------
echo
echo "================================================================"
echo " RESULT"
echo "================================================================"

# Odoo's own end-of-run summary line is the source of truth:
#   "N failed, M error(s) of T tests when loading database '…'"
# Trust it when present — bare "Traceback" greps misfire on tests that
# intentionally exercise error paths (they log a traceback on purpose).
SUMMARY=$(grep -aE "[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests" "$LOG" | tail -n 1)

if [ -n "$SUMMARY" ]; then
  # Parse "X failed, Y error(s) of Z tests" — PASS iff X=0 and Y=0.
  FAILED_N=$(echo "$SUMMARY" | sed -nE 's/.*([0-9]+) failed, ([0-9]+) error.*/\1/p')
  ERRORS_N=$(echo "$SUMMARY" | sed -nE 's/.*([0-9]+) failed, ([0-9]+) error.*/\2/p')
  FAILS=$((FAILED_N + ERRORS_N))
else
  # No summary → odoo-bin probably never reached the test phase. Fall back
  # to marker grep + exit code so silent failures (binary missing, port
  # taken, registry crash) don't slip through as a false PASS.
  FAILS=$(grep -cE "(FAIL:|ERROR [0-9].*odoo\.tests|CRITICAL .*odoo\.tests|Traceback \(most recent call last\)|Address already in use|Port [0-9]+ is in use|command not found)" "$LOG")
fi
[ "${ODOO_EXIT:-0}" -ne 0 ] && FAILS=$((FAILS + 1))

if [ -n "$SUMMARY" ]; then
  echo "Odoo summary:"
  echo "$SUMMARY" | sed 's/^/   /'
fi

if [ "$FAILS" -eq 0 ]; then
  echo
  echo "✅ PASS — no test failures, errors, or tracebacks found."
  echo "   (Full log: $LOG)"
  exit 0
else
  echo
  echo "❌ FAIL — $FAILS failure/error marker(s) found. First offenders:"
  grep -nE "(FAIL:|ERROR [0-9].*odoo\.tests|CRITICAL .*odoo\.tests|Traceback \(most recent call last\))" "$LOG" \
    | head -n 20 | sed 's/^/   /'
  echo
  echo "   Full log: $LOG   (search it for 'FAIL:' / 'Traceback')"
  exit 1
fi
