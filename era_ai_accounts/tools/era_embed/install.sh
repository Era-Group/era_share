#!/bin/bash
# Bootstrap the local embeddings service on a new server.
#
# Everything a fresh machine needs, in one command:
#   bash install.sh
#
# It is idempotent — re-running it repairs a partial install and skips whatever
# is already in place. It does NOT touch Odoo: see the end of this script for
# the two remaining steps a machine cannot do for itself.
set -euo pipefail

DIR="${ERA_EMBED_DIR:-/var/lib/odoo/era_embed}"
MODEL="${ERA_EMBED_MODEL:-intfloat/multilingual-e5-large}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${ERA_EMBED_PORT:-8091}"

say() { echo "==> $*"; }

# The model is ~2.2GB on disk and the venv another ~0.5GB. Failing here is far
# kinder than failing halfway through a download.
need_mb=3500
avail_mb=$(df -Pm "$(dirname "$DIR")" | awk 'NR==2 {print $4}')
if [ "$avail_mb" -lt "$need_mb" ]; then
    echo "Not enough space: ${avail_mb}MB free, need ~${need_mb}MB" >&2
    exit 1
fi

say "installing into $DIR"
mkdir -p "$DIR/models"
cp "$SRC/server.py" "$DIR/server.py"
cp "$SRC/start.sh" "$DIR/start.sh"
chmod +x "$DIR/start.sh"

if [ ! -x "$DIR/venv/bin/python" ]; then
    say "creating the virtualenv (kept separate from Odoo's on purpose)"
    python3 -m venv "$DIR/venv"
fi
say "installing python dependencies"
"$DIR/venv/bin/pip" install -q --disable-pip-version-check -r "$SRC/requirements.txt"

# Fetch the weights now rather than on the first user-facing request, which
# would otherwise time out while 2.2GB downloads.
say "downloading $MODEL (~2.2GB, once)"
ERA_EMBED_CACHE="$DIR/models" "$DIR/venv/bin/python" - "$MODEL" "$DIR/models" <<'PY'
import sys
from fastembed import TextEmbedding
model, cache = sys.argv[1], sys.argv[2]
m = TextEmbedding(model, cache_dir=cache)
v = next(iter(m.embed(["passage: اختبار المحتوى العربي"])))
print(f"    model ready — {len(v)} dimensions")
PY

say "starting the service"
setsid "$DIR/start.sh" >> "$DIR/server.log" 2>&1 &
for _ in $(seq 1 30); do
    curl -sf --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
    sleep 1
done
if curl -sf --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    say "service healthy on 127.0.0.1:$PORT"
else
    echo "Service did not come up — see $DIR/server.log" >&2
    exit 1
fi

cat <<EOF

Done. Two steps remain that this script cannot do for you:

  1. Survive a container restart — add to the container entrypoint:

       setsid $DIR/start.sh >> $DIR/server.log 2>&1 &

  2. Point Odoo at it — Settings > Technical > System Parameters,
     or run the "Use the local embeddings service" action shipped with
     era_ai_accounts:

       ai.custom_llm_base_url        http://127.0.0.1:$PORT/v1
       ai.custom_llm_key             local
       ai.custom_llm_embedding_model $MODEL
       ai.embedding_model_override   custom_llm/text-embedding-3-small

EOF
