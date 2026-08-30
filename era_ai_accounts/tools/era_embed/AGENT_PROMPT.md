# Prompt: stand up the shared embeddings server

Paste the block below to an agent on the **new, standalone server**.
Fill in the three placeholders first.

---

Set up a shared embeddings service on this server. It will be the single
embedding endpoint for our Odoo instances, so no Odoo host has to install a
model of its own.

**Background.** Our Odoo AI agents sign in through the Claude and codex CLIs.
Those are text-only — they have no embeddings endpoint — so knowledge sources
can never be indexed through them. Rather than buy a key-based subscription
just to index documents, we serve embeddings ourselves. The service speaks the
OpenAI `POST /v1/embeddings` shape, so Odoo reaches it through its existing
`custom_llm` provider by configuration alone.

**Get the code.** It lives in the `era_share` repository, module
`era_ai_accounts`, directory `tools/era_embed/`. Clone the repo (branch `19.0`)
or copy that directory over. Read `docs/LOCAL_EMBEDDINGS.md` in the same module
before you start — it explains the design decisions you must not undo.

**Install.**

```bash
export ERA_EMBED_HOST=<the private IP this server should listen on>
export ERA_EMBED_TOKEN=<generate one: openssl rand -hex 32>
bash tools/era_embed/install.sh
```

The script is idempotent: it checks free disk before downloading ~2.2GB of
weights, creates a virtualenv deliberately separate from Odoo's, prefetches the
model so the first real request does not time out, and starts the service under
a supervisor.

**Requirements this host must meet.**

- ~4GB free disk (2.2GB weights + virtualenv), ~2GB RAM while serving.
- Python 3.10+ and outbound HTTPS to huggingface.co for the one-time download.
- Reachable from the Odoo hosts on the chosen port (default 8091).

**Security — do not skip.** The service refuses to listen on anything but
loopback unless `ERA_EMBED_TOKEN` is set; that guard is deliberate, do not
remove it. Additionally, firewall the port so only our Odoo hosts can reach it.
An open inference endpoint is both free compute for strangers and a window into
what a law firm is indexing. `/health` stays unauthenticated on purpose so
monitoring works; it returns no data.

**Make it survive a reboot.** There is no systemd in our Odoo container, but
this is a standalone server — if it has systemd, write a unit for
`/var/lib/odoo/era_embed/start.sh` rather than relying on an entrypoint line.
`start.sh` already supervises the process, holds a `flock` so two supervisors
cannot fight, truncates its own log, and restarts the server if it dies. It
exits cleanly (code 3) if another instance already holds the port.

**Verify before reporting success.**

1. `curl http://<host>:8091/health` returns `{"status": "ok", ...}`.
2. An authenticated embedding call returns **1536** dimensions:

   ```bash
   curl -s -X POST http://<host>:8091/v1/embeddings \
     -H "Authorization: Bearer $ERA_EMBED_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"input":["passage: نظام المحاماة"],"dimensions":1536}' \
     | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]))'
   ```

   1536 rather than the model's native 1024 is correct: Odoo's vector column is
   a fixed `Vector(size=1536)`, and the service zero-pads. Padding leaves cosine
   similarity exactly unchanged, so the index keeps working. If you see 1024,
   something is wrong — do not "fix" it by changing Odoo's column.
3. The same call **without** the Authorization header returns 401.
4. Kill the server process and confirm the supervisor restarts it within ~15s.

**Report back:** the URL, the port, the token, and the output of all four
checks. I will then point Odoo at it — that side is four system parameters and
needs nothing installed.
