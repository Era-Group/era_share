# ERA AI Accounts

Link AI **provider accounts** to Odoo 19's standard `ai` / `ai_app` stack, share
them with users, and pick models dynamically per account. Supersedes
`era_odoo_ai_ext` (whose custom_llm provider is absorbed here).

## Why

The stock `ai` module hard-codes one API key per provider, supports only
OpenAI/Google, and exposes a fixed model list. This module adds:

- **Claude via local CLI proxy** — route Claude through the Claude Code `claude`
  binary that is already authenticated on this server (the "connected account"),
  so AI works **without per-token API billing**. The first-party CLI performs the
  call under its own auth; the module never reads or replays its credentials.
- **OpenAI (ChatGPT account) via Codex CLI proxy** — the same idea for OpenAI:
  route chat through the first-party `codex` binary signed in with a ChatGPT
  subscription (Plus/Pro/Business/Edu/Enterprise), **no API key**. See
  "OpenAI via Codex CLI" below.
- **Kimi (Moonshot AI) via Kimi Code CLI proxy** — route chat through the
  first-party `kimi` binary in print mode, fenced down to pure text generation.
  See "Kimi (Moonshot AI)" below.
- **API-key accounts** — OpenAI, Google Gemini, Anthropic (Messages API),
  **Cloudflare Workers AI**, **Z.AI (GLM)**, **Kimi (Moonshot AI)**, and any
  OpenAI-compatible custom endpoint, with secrets stored **encrypted** and
  restricted to *AI Account Managers*.
- **Shared vs personal accounts** — one account for everyone or per-user accounts,
  with `owner` + `allowed users` + record rules controlling who may use each.
- **Dynamic model catalog** — synced from each account (curated set for the CLI
  proxy and Cloudflare; live `/models` for key accounts).

## Using an account from code

Any module can drive a configured account directly — no `ai.agent` needed:

- `account.generate_text(prompt, system="", model=None)` → `str` — chat/content
  through the account's provider (CLI proxy, OpenAI, Cloudflare, …).
- `account.generate_image(prompt, model=None, steps=None)` → image **bytes** —
  Cloudflare Workers AI (FLUX.1-schnell, FLUX.2 dev/klein, SDXL) or OpenAI
  (`gpt-image-1`, `dall-e-3`). Sync models to pick a specific image model.
- `account.transcribe(audio, model=None, language=None)` → transcript **str** —
  speech-to-text for **OpenAI** API-key accounts (`gpt-4o-transcribe`,
  `gpt-4o-mini-transcribe`, `whisper-1`). `audio` is the raw file bytes;
  `language` is an optional ISO-639-1 hint. The Claude/Codex CLI proxies are
  text-only and have no speech endpoint, so transcription is OpenAI-only.

This is how other ERA modules (e.g. `era_seo_suite`) let an admin pick *one
account for content* and *one account for images* instead of re-entering
provider/key/model settings in each module.

## OpenAI via Codex CLI (ChatGPT account, no API key)

Pick provider **OpenAI** with auth mode **Local CLI proxy**. Requirements: the
`codex` binary on the server (`npm i -g @openai/codex`; or set the account's
*CLI binary path* / `ERA_AI_CODEX_BIN`) and a ChatGPT plan with Codex access
(Plus, Pro, Business, Edu or Enterprise).

Linking the account (manager-only, click **Connect ChatGPT account**), two ways:

1. **Device login (simplest, like signing in on a TV):** click *Start device
   login* — the wizard runs `codex login --device-auth` on the server and shows
   a link plus a one-time code. Open the link on any device, enter the code,
   approve, then click *Check status*. Requires *device codes* to be enabled in
   the ChatGPT account's security settings (the code expires in ~15 minutes).
2. **Paste `auth.json` (fallback):** OpenAI's OAuth client only redirects to
   `localhost:1455` (no hosted copy-code page like Claude's), so the manual
   flow uses OpenAI's **officially documented server/CI pattern** — run
   `codex login` on your own computer (choose *Sign in with ChatGPT*), then
   paste the contents of `~/.codex/auth.json` into the dialog.

Either way the credentials land in an isolated per-account `CODEX_HOME`
(`<data_dir>/era_ai_accounts/cli/<id>/.codex/auth.json`, mode 0600), and the
codex CLI refreshes/rotates the tokens in place during use. If no account is
linked, the CLI falls back to the server's own `~/.codex` login under the
account's *CLI HOME*. **Validate connection** runs `codex login status` — it
confirms both the binary and the credentials without spending tokens.

Generation runs through `codex exec` locked down to **pure text**: read-only
sandbox, shell tool disabled, web search disabled, user `config.toml` ignored,
no session files (`--ephemeral`). Tool-calling, embeddings and images are not
available through this transport (same v1 limitation as the Claude proxy).
Models are a curated catalog (`gpt-5.4`, `gpt-5.4-mini`) — ChatGPT-plan auth
has no model-list endpoint, and the codex-tuned slugs (`gpt-5.x-codex`) are
**rejected** when passed explicitly under ChatGPT-account auth (verified live);
edit the rows if your plan serves different slugs. The Codex pool is **hard-clamped to one call at a time**
(regardless of `ai.cli_max_concurrency`, which still sizes the Claude pool):
codex rewrites `auth.json` when it refreshes tokens, and two concurrent
refreshes would race last-writer-wins and break the linked account.

**Heads-up (ToS):** OpenAI's own docs restrict ChatGPT-account auth to
*trusted private infrastructure* and recommend API keys for most server/CI
use; ChatGPT subscriptions are personal and rate-limited in rolling 5-hour +
weekly windows shared with any other Codex use of that account. Same posture
as the Claude compliance note below — keep the kill switch / pacing on, and
prefer an API-key account for heavy or commercial-scale traffic.

## Cloudflare Workers AI

Pick provider **Cloudflare Workers AI** (auth: API key), set the **Cloudflare
Account ID** (it goes in the URL path) and paste an **API token**. Then **Sync
models** for a curated catalog of chat, image (FLUX.1-schnell) and embedding
models. Cloudflare bills in **Neurons** with **10,000 free per day**; it does
**not** expose per-model price via its API, so each synced model shows an
*indicative* Neuron rate captured from the public pricing page — confirm the
live rate at <https://developers.cloudflare.com/workers-ai/platform/pricing/>.
Content runs through Cloudflare's OpenAI-compatible `/ai/v1/chat/completions`;
images through `/ai/run/@cf/black-forest-labs/flux-1-schnell`.

## Z.AI (GLM / Zhipu) — API key *or* CLI proxy

Z.AI serves the GLM model family (GLM-4.6/4.7, GLM-5.x, plus free Flash models)
through **two** surfaces, and this module supports **both** — the same Z.AI API
key works for either. Pick **provider Z.AI (GLM / Zhipu)**, then choose the auth
mode:

1. **API key** (auth mode *API key*) — OpenAI-compatible chat at
   `https://api.z.ai/api/paas/v4` (`/chat/completions`, Bearer auth). Paste the
   key, **Validate connection** (token-free `/models` check), **Sync models**
   for the curated GLM catalog with indicative USD rates. This route supports
   **native tool-calling**, so it can drive tool-using agents — not just chat.
   Billed **per token in USD** (see the
   [pricing page](https://docs.z.ai/guides/overview/pricing)).

2. **CLI proxy** (auth mode *Local CLI proxy*) — the **GLM Coding Plan** flow:
   the local `claude` binary is pointed at Z.AI's **Anthropic-compatible**
   endpoint (`https://api.z.ai/api/anthropic`) with your key exported as
   `ANTHROPIC_AUTH_TOKEN`. This is a **flat-rate subscription** (no per-token
   billing), serving every user through that one key. Paste the key in the *CLI
   proxy* box, **Validate** (checks the `claude` binary **and** the key),
   **Sync models** for the GLM list. Z.AI maps the Claude tiers to GLM models
   server-side via `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL`, so the
   transport **pins all three to the selected GLM model and passes no
   `--model`** (Claude Code rejects a non-Claude name there). No "Login with
   Claude" / OAuth is involved — the key alone authenticates. The same
   host-wide concurrency cap, pacing gap and kill switch as the other CLI
   proxies apply (it shares the Claude binary's slot pool). Tools work through
   the JSON-envelope tool loop, gated by **Allow agent tools**. *Requirement:*
   the `claude` binary must be present and its **CLI HOME** onboarded (the same
   one the Claude proxy uses); with an explicit auth token Claude Code runs
   non-interactively against Z.AI.

Image generation and transcription are **not** offered for Z.AI here (use
Cloudflare/OpenAI for those).

## Kimi (Moonshot AI) — API key *or* Kimi CLI proxy

Kimi serves the K3 / K2.x family through two surfaces, and — as with Z.AI — the
**same key works for both**. Pick **provider Kimi (Moonshot AI)**, then the auth
mode:

1. **API key** (auth mode *API key*) — OpenAI-compatible chat at
   `https://api.moonshot.ai/v1` (`/chat/completions`, Bearer auth). Paste the
   key, **Validate connection** (token-free `/models` check), **Sync models**
   for the curated Kimi catalog with indicative USD rates. This route supports
   **native tool-calling**, so it can drive tool-using agents. Set *API base URL*
   to `https://api.moonshot.cn/v1` for the China region, or to a gateway.

2. **CLI proxy** (auth mode *Local CLI proxy*) — Moonshot's first-party **Kimi
   Code CLI**. Install it as the `odoo` user with

   ```
   bash /opt/odoo/submodules/era_share_latest/era_ai_accounts/tools/ensure-kimi-cli.sh
   ```

   That helper ships with this module, so **every server gets it from the git
   clone** — put exactly that line in the cicdoo startup script and no new
   server needs hand-setup. It is idempotent, and it exists because the startup
   script runs **as root** while the installer writes to `$HOME`: a plain
   `curl … | bash` there lands the binary in `/root/.kimi-code`, which the
   `odoo` user cannot even read. The helper re-executes itself as `odoo`
   (`su -`; neither `sudo` nor `runuser` is in the image), removes any stray
   root-owned copy, skips the download when a working binary is present, deletes
   the 172 MB `.bak` the installer leaves behind, and finishes with a
   `--version` run — the only check that catches a too-old glibc, which installs
   cleanly and fails only at run time.

   The binary lands in `~/.kimi-code/bin/kimi` and is auto-detected (no PATH
   change needed; Odoo's service PATH would not pick one up anyway). The older
   Python package (`uv tool install kimi-cli`, entry point in `~/.local/bin`) is
   also detected but is **deprecated upstream**. To point elsewhere, set the
   account's *CLI binary path* or `ERA_AI_KIMI_BIN`.

   Requirements: Linux/macOS, x64/arm64, **glibc ≥ 2.28**, not musl,
   `curl` + `sha256sum`, ~175 MB free.

   An **API key is required** even in CLI mode: `kimi login` is an interactive
   device-code flow that cannot be driven from Odoo. The key is passed to the
   CLI through the environment on every call and never written to any file.
   **Validate connection** runs `kimi --version` plus the token-free `/models`
   check; **Sync models** gives the curated Kimi list.

### Linking a Kimi Code subscription (no key to handle)

Click **Connect Kimi account** on a Kimi CLI-proxy account. The wizard runs
`kimi login` on the server, which prints a verification URL plus a one-time code
(valid ~30 minutes) and then polls; open the link on any device, sign in to the
Kimi account holding the subscription, enter the code, approve, then click
**Check status**. Same flow as *Connect ChatGPT account*.

The CLI itself writes the link into the account's managed `KIMI_CODE_HOME` — a
provider carrying an `oauth` ref plus the model aliases the plan serves — and
that file is what Odoo reads to decide the account is linked. So after linking:

* **no API key is needed or stored** (the key box disappears);
* **Sync models** lists the aliases the *subscription* provisions, not a curated
  guess, and the plan's own `default_model` becomes the default;
* calls select the model with `-m <alias>` and export **no** `KIMI_MODEL_*` —
  those would synthesize a private provider and silently bypass the
  subscription;
* the per-call fence rewrite **merges** into `config.toml`: only `[tools]` and
  `[loop_control]` are re-emitted, and everything the login provisioned is
  copied through byte for byte. Without that, the first call after a login would
  erase it.

*Not detected:* token expiry. The CLI refreshes its own token, and it exposes no
token-free credential probe, so there is no "link expired" banner for Kimi — a
dead login surfaces as the CLI's own error on the next call. **Disconnect**
removes the provisioned config and returns the account to key-based auth.

An API key remains supported and is Moonshot's documented route for third-party
integrations; the table below covers where each key comes from.

### Which Kimi key: plan or platform?

Two separate products with **separate billing** — a consumer kimi.com chat
subscription funds neither. Pick the matching *Kimi plan* on the account:

| | **Kimi Code plan** (`coding`) | **Open Platform** (`platform`) |
|---|---|---|
| Get the key at | [kimi.com/code](https://www.kimi.com/code) | [platform.kimi.ai](https://platform.kimi.ai) |
| Billing | flat monthly subscription | per token (K2.6/K2.7-code ≈ $0.95 in / $4 out per 1M; K3 ≈ $3 / $15) |
| Endpoint | `api.kimi.com/coding/v1` | `api.moonshot.ai/v1` |
| Wire protocol | **Anthropic** (`POST /messages`) | OpenAI (`/chat/completions`) |
| Model ids | `kimi-for-coding`, `kimi-for-coding-highspeed`, `k3`, `k3-256k` | `kimi-k2.6`, `kimi-k3`, `kimi-k2.7-code`, `kimi-k2.5` |
| Auth mode | **CLI proxy only** | CLI proxy *or* API key |

The plan runs through the CLI proxy because its endpoint is Anthropic-shaped and
this module's HTTP transport speaks OpenAI chat-completions; the CLI selects the
protocol per provider, so it handles both. This mirrors Z.AI's GLM Coding Plan.
**Sync models after switching plans** — the two surfaces share no model ids, and
the account refuses `coding` in API-key mode.

The plan's usage quota is shared with any other use of that Kimi account, so the
kill switch and pacing apply as with the other subscription-backed providers.

> **The shipped binary does not match Moonshot's published docs.** Everything
> below was verified live against **kimi-code 0.36.1**; the docs still describe
> the older Python `kimi-cli`. There is no `--print`, `--config`, `--work-dir`,
> `--max-steps-per-turn` or `--final-message-only`, stdin is rejected
> ("Output format is only supported in prompt mode"), and the credential env
> vars are `KIMI_MODEL_API_KEY` / `KIMI_MODEL_BASE_URL` / `KIMI_MODEL_NAME` —
> not the documented `KIMI_API_KEY` / `KIMI_BASE_URL`. **Re-verify after a CLI
> upgrade rather than trusting the documentation.**

### How one call is assembled

`kimi -p <user text> --output-format stream-json`, with:

| Piece | Why |
|---|---|
| `KIMI_MODEL_API_KEY` / `_BASE_URL` / `_NAME` / `_PROVIDER_TYPE` / `_MAX_CONTEXT_SIZE` | synthesize a private provider + model alias that the CLI makes the default — so no `-m` (which wants an alias declared in the config's `[models]` table) and no key on disk |
| an isolated `KIMI_CODE_HOME` per account, mode 0700 | never the operator's own `~/.kimi-code` |
| `config.toml` → `[tools] enabled = ["EraAiAccountsNoTools"]` | prompt mode otherwise offers the model **all 25 built-in tools — `Bash`, `Write`, `Edit`, `CronCreate` included** (measured). A non-empty `enabled` is an allowlist, and a name matching no tool leaves it empty: **25 → 0 tools** |
| `config.toml` → `[loop_control] max_steps_per_turn = 1` | bounds the agent loop |
| `SYSTEM.md` | wholly replaces the built-in coding-agent persona — this is both how Odoo's system prompt is injected and how a ~21 KB preamble is dropped (**20,959 → 69 chars** for a bare call) |
| a fresh empty **cwd** | there is no `--work-dir` flag; the working directory *is* the workspace, so file tools reach nothing and no project context (`AGENTS.md`, `KIMI.md`, the Odoo tree) is loaded |

Both files are rewritten before every call, so an edited or stale one cannot
weaken the fence. `kimi doctor` validates the generated `config.toml`.

**Prompt size:** the user turn rides in `argv`, which Linux caps at 128 KiB per
argument (verified: 127 KB accepted, 130 KB → `E2BIG`). Odoo refuses anything
over 96 KB with a clear message. Only the *user* turn is affected — the bulk
(RAG context, tool instructions) goes to `SYSTEM.md`, which is a file.

**Concurrency:** Kimi has its own slot pool
(`<data_dir>/era_ai_cli_proxy.kimi.<n>.lock`) so it never queues behind Claude
or Codex, but it is **hard-clamped to one call at a time** regardless of
`ai.cli_max_concurrency` — `config.toml` and `SYSTEM.md` are per-account
single-writer, and two concurrent calls would race each other's system prompt.

**Housekeeping:** each call creates a session record under the account's
`KIMI_CODE_HOME` (session index, caches, search index — a few KB per call).
Deleting the account removes the whole directory.

Image generation and transcription are **not** offered for Kimi here (use
Cloudflare/OpenAI for those).

## Configure

1. **AI ▸ AI Accounts ▸ New** (managers only).
2. Pick a **provider** and **auth mode**:
   - *Local CLI proxy* (Anthropic or OpenAI): optionally set the CLI binary
     path / `HOME`; no key needed. **Or link a subscription in-app** — *"Login
     with Claude"* (OAuth, manual copy-code flow) or *"Connect ChatGPT
     account"* (paste the Codex CLI's `auth.json`): the credentials are stored
     once on the server — in an isolated directory, never touching the server's
     own `~/.claude` / `~/.codex` login — and **every user** runs through that
     one account. *Re-link* / *Disconnect* buttons manage it; managers only.
   - *API key*: paste the provider key (write-only, stored encrypted). For a
     *custom* provider also set base URL / auth header.
3. **Validate connection**, then **Sync models**.
4. Set **scope** (shared/personal), **owner**, and **allowed users**.
5. On an **AI Agent**, set **AI Account** + **Account Model**. Responses are then
   generated through the account.

### Link health (linked subscriptions)

A linked subscription has four on-disk states, shown on the account form
(`cli_link_state`):

- **Linked** (`active`) — a usable access token is present.
- **Linked (renewing)** (`stale`) — the short-lived access token lapsed but a
  refresh token remains; the first-party CLI renews it in place on the next
  call. Still fully usable, still routed to the linked account — the module
  keeps using the managed credential dir so that renewal can happen (this is how
  a link survives the access token's ~hourly expiry).
- **Link expired** (`expired`) — the login is dead (no refresh possible). A red
  banner asks a manager to **re-link**. By default (`era_ai_accounts.cli_link_strict`)
  AI calls through the account **fail loudly** rather than silently borrowing the
  server's own ambient login — so a dead shared link is fixed, not hidden.
- **Not linked** (`none`) — no in-app link; the CLI uses the server's ambient
  `~/.claude` / `~/.codex` login under the account's *CLI HOME*, on purpose.

> After re-linking (browser OAuth for Claude / device-code or `auth.json` for
> ChatGPT), the state returns to *active* for every user immediately.

## Server persistence (`tools/`)

Two boot-time helpers ship with this module. Both are idempotent, both
re-execute themselves as `odoo` (the cicdoo startup script runs as **root**, and
`$HOME` is what decides where things land), and both belong on one line each in
the platform's startup-script field:

```
bash /opt/odoo/submodules/era_share_latest/era_ai_accounts/tools/ensure-kimi-cli.sh
bash /opt/odoo/submodules/era_share_latest/era_ai_accounts/tools/ensure-claude-persistent-home.sh
```

* **`ensure-kimi-cli.sh`** — installs the Kimi Code CLI where Odoo can find it
  (see the Kimi section above).
* **`ensure-claude-persistent-home.sh`** — makes `/opt/odoo/.claude` a symlink to
  `/var/lib/odoo/claude-home`, so Claude Code's transcripts, memory and login
  survive a container rebuild. On this host only `/var/lib/odoo`,
  `/var/log/odoo` and `/opt/odoo/{ce,ee,themes}` are real mounts; everything
  else is overlay and is destroyed on rebuild. A symlink is used rather than
  `CLAUDE_CONFIG_DIR` alone because the VS Code extension spawns the CLI
  directly, never through a shell, so no `~/.bashrc` export can reach it — the
  script sets the env var too, for shell-launched `claude`, pointing at the same
  directory. It refuses to migrate while Claude is running and keeps the old
  directory as `.claude.migrated.<epoch>`.

## Compliance note

Using a **subscription** (Claude Pro/Max or ChatGPT Plus/Pro/…) through the
local CLI to serve many Odoo users draws on that subscription's rate limits and
intended-use terms. The per-account **concurrency cap** and **kill switch**
keep this controllable. Replaying the CLIs' OAuth tokens directly against
`api.anthropic.com` / `api.openai.com` is **not** done — the first-party
binaries make every call under their own auth, and direct replay is blocked
and violates the providers' ToS.

## Agent tools through the CLI proxy ("Ask AI" data extraction)

CLI-proxy accounts run Odoo's **agent tools** (e.g. the standard Ask AI agent's
database lookups) through a JSON tool-call loop: the tools are described in the
prompt, the model replies with a strict
`{"tool_calls": [{"name": ..., "arguments": {...}}]}` envelope, Odoo executes
the tool **in-process with the requesting user's own access rights** (the same
upstream execution path as API-key providers — same caps,
`ai.max_successive_calls`), feeds the result back, and repeats until the model
answers in plain text. The CLI subprocess only ever sees serialized text; no
credentials or ORM access cross the boundary. Each tool round is one extra CLI
call (serialized + paced as usual), so multi-step questions take longer than
API-key accounts. Per-account switch: **Allow agent tools** (on by default; turn
off to keep an account strictly single-shot chat).

## Limitations (v1)
- No CLI embeddings: for agents with **knowledge sources**, keep the agent's
  *LLM Model* on OpenAI/Gemini (used only for embeddings); generation still
  goes through the account.
- The CLI proxies have **no image generation** — they produce text only. For
  blog/article cover images, create a **Cloudflare** or **OpenAI** (`api_key`)
  account and sync its models — `era_seo_suite`'s *Blog Gen* tab then lets you
  pick that account (and a specific image model) directly. Use the account's
  **Note** field to record what it is linked for.
- The `gemini` CLI is not bridged, so Google Gemini uses API keys.
- Kimi link health is binary (linked / not linked): unlike Claude and ChatGPT
  there is no readable expiry, so no "link expired" banner — see above.

## Security

- Secrets are Fernet-encrypted at rest. Set `ERA_AI_SECRET_KEY` (env / odoo.conf)
  to control the key; otherwise `database.secret` is used. The secret field is
  restricted to `era_ai_accounts.group_ai_account_manager`.
- Shared users get a *relation* to an account, never its secret — all calls run
  server-side.

## Configuration parameters

| `ir.config_parameter` | Default | Purpose |
|---|---|---|
| `ai.cli_timeout` | 180 | CLI-proxy subprocess timeout (s) |
| `ai.cli_max_prompt_chars` | 400000 | Max system+user prompt size for the CLI proxy; larger → clear error (use an API-key account) |
| `ai.cli_gap_enabled` | True | Master toggle for the inter-call pacing gap |
| `ai.cli_min_gap` | 1.0 | Base gap (s) enforced between consecutive CLI calls |
| `ai.cli_gap_per_kb` | 0.05 | Extra gap (s) per KB of request body — bigger requests wait longer |
| `ai.cli_max_gap` | 30 | Cap (s) on the inter-call gap |
| `ai.cli_max_concurrency` | 1 | Max simultaneous CLI calls host-wide, per provider pool (1 = strictly one at a time). Codex and Kimi are always 1 |
| `ai.cli_lock_wait` | 300 | Max time (s) a request waits for a free slot before erroring |
| `ai.http_timeout` | 120 | Anthropic HTTP timeout (s) |
| `ai.anthropic_max_tokens` | 4096 | `max_tokens` for the Messages API |
| `ai.openai_image_timeout` | 300 | OpenAI image-generation HTTP timeout (s) — high-quality `gpt-image-1` renders can take minutes |
| `ai.openai_transcribe_timeout` | 120 | OpenAI speech-to-text HTTP timeout (s) for `account.transcribe()` — raise it for long recordings |
| `era_ai_accounts.cli_link_strict` | True | If a **linked** subscription expires, fail the call (True) instead of silently using the server's own ambient login. Set to `False` only for a temporary zero-downtime window while a manager re-links. |

All of the `ai.cli_*` settings above are editable in the UI: **Settings ▸ AI ▸ "AI
CLI rate protection"** (no redeploy needed). They apply to both CLI providers.

> **Throttling (gentle on the connected account):** CLI-proxy calls are throttled by a
> host-wide cross-process semaphore of `ai.cli_max_concurrency` slots (default **1** = at
> most one call at a time across every Odoo worker and user; raise it to allow controlled
> concurrency). Each provider has its own slot pool — a Claude call never queues behind a
> Codex call. Lock files live under `<data_dir>/era_ai_cli_proxy.<n>.lock` (Claude) and
> `<data_dir>/era_ai_cli_proxy.codex.<n>.lock` (Codex) and auto-release if a worker dies.
> When `ai.cli_gap_enabled` is on, consecutive calls are also separated by a gap that
> **scales with the request body size** (`min_gap + gap_per_kb × KB`, capped at `max_gap`),
> so large requests wait longer. The Codex pool is fixed at **1 slot** regardless of the
> concurrency setting — `auth.json` is single-writer (tokens rotate on refresh).

> **Note (memory):** Odoo applies a soft `RLIMIT_AS` (= `limit_memory_hard`) to its
> workers; the CLI's JS runtime needs far more *virtual* address space than that and
> would abort with a `MemoryExhaustion` assertion. The transport raises the child's
> soft limit back to the (unlimited) hard limit via `preexec_fn`, so the CLI uses the
> host's real RAM. No change to the Odoo worker's own limit.
