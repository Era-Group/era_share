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

MODULES="era_seo_manager,era_seo_blog,era_seo_ai,era_seo_blog_ai,era_geo"
# `/module` selects every test in that module regardless of its @tagged value.
TEST_TAGS="/era_seo_manager,/era_seo_blog,/era_seo_ai,/era_seo_blog_ai,/era_geo"
ODOO_BIN="${ODOO_BIN:-odoo-bin}"
LOG="${LOG:-/tmp/era_seo_verify_$(date +%Y%m%d_%H%M%S).log}"

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
"$ODOO_BIN" -d "$DB" \
  -u "$MODULES" \
  --test-enable \
  --test-tags "$TEST_TAGS" \
  --stop-after-init \
  --log-level=test 2>&1 | tee "$LOG"

# --- Verdict ---------------------------------------------------------------
echo
echo "================================================================"
echo " RESULT"
echo "================================================================"

# Odoo logs failures/errors at level ERROR/CRITICAL; assertion failures show
# "FAIL:" and exceptions show "ERROR ...: Traceback". Count the real markers.
FAILS=$(grep -cE "(FAIL:|ERROR [0-9].*odoo\.tests|CRITICAL .*odoo\.tests|Traceback \(most recent call last\))" "$LOG")
# Odoo's own end-of-run summary line, if present.
SUMMARY=$(grep -aiE "tests? when loading|[0-9]+ failed, [0-9]+ error" "$LOG" | tail -n 5)

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
