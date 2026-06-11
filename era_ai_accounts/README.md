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
- **API-key accounts** — OpenAI, Google Gemini, Anthropic (Messages API),
  **Cloudflare Workers AI**, and any OpenAI-compatible custom endpoint, with
  secrets stored **encrypted** and restricted to *AI Account Managers*.
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

This is how other ERA modules (e.g. `era_seo_suite`) let an admin pick *one
account for content* and *one account for images* instead of re-entering
provider/key/model settings in each module.

## OpenAI via Codex CLI (ChatGPT account, no API key)

Pick provider **OpenAI** with auth mode **Local CLI proxy**. Requirements: the
`codex` binary on the server (`npm i -g @openai/codex`; or set the account's
*CLI binary path* / `ERA_AI_CODEX_BIN`) and a ChatGPT plan with Codex access
(Plus, Pro, Business, Edu or Enterprise).

Linking the account (manager-only, click **Connect ChatGPT account**): OpenAI's
OAuth client only redirects to `localhost:1455` (there is no hosted copy-code
page like Claude's), so the in-app flow uses OpenAI's **officially documented
server/CI pattern** instead — run `codex login` on your own computer (choose
*Sign in with ChatGPT*), then paste the contents of `~/.codex/auth.json` into
the dialog. The file is stored under an isolated per-account `CODEX_HOME`
(`<data_dir>/era_ai_accounts/cli/<id>/.codex/auth.json`, mode 0600), and the
codex CLI refreshes/rotates the tokens in place during use. If no account is
linked, the CLI falls back to the server's own `~/.codex` login under the
account's *CLI HOME* (e.g. after running `codex login --device-auth` on the
server). **Validate connection** runs `codex login status` — it confirms both
the binary and the credentials without spending tokens.

Generation runs through `codex exec` locked down to **pure text**: read-only
sandbox, shell tool disabled, web search disabled, user `config.toml` ignored,
no session files (`--ephemeral`). Tool-calling, embeddings and images are not
available through this transport (same v1 limitation as the Claude proxy).
Models are a curated catalog (`gpt-5.3-codex`, `gpt-5.4`, `gpt-5.4-mini`) —
ChatGPT-plan auth has no model-list endpoint; edit the rows if your plan serves
different slugs. Keep `ai.cli_max_concurrency` at 1: codex rewrites `auth.json`
when it refreshes tokens, and the per-provider one-at-a-time lock prevents two
processes racing a refresh.

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

## Compliance note

Using a **subscription** (Claude Pro/Max or ChatGPT Plus/Pro/…) through the
local CLI to serve many Odoo users draws on that subscription's rate limits and
intended-use terms. The per-account **concurrency cap** and **kill switch**
keep this controllable. Replaying the CLIs' OAuth tokens directly against
`api.anthropic.com` / `api.openai.com` is **not** done — the first-party
binaries make every call under their own auth, and direct replay is blocked
and violates the providers' ToS.

## Limitations (v1)

- The CLI proxies (Claude and Codex) support **chat / RAG answers** only —
  Odoo's tool-calling "Ask-AI navigation" tools are not bridged through the
  CLIs (use an API-key account for tool-using agents).
- No CLI embeddings: for agents with **knowledge sources**, keep the agent's
  *LLM Model* on OpenAI/Gemini (used only for embeddings); generation still
  goes through the account.
- The CLI proxies have **no image generation** — they produce text only. For
  blog/article cover images, create a **Cloudflare** or **OpenAI** (`api_key`)
  account and sync its models — `era_seo_suite`'s *Blog Gen* tab then lets you
  pick that account (and a specific image model) directly. Use the account's
  **Note** field to record what it is linked for.
- The `gemini` CLI is not bridged, so Google Gemini uses API keys.

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
| `ai.cli_max_concurrency` | 1 | Max simultaneous CLI calls host-wide (1 = strictly one at a time) |
| `ai.cli_lock_wait` | 300 | Max time (s) a request waits for a free slot before erroring |
| `ai.http_timeout` | 120 | Anthropic HTTP timeout (s) |
| `ai.anthropic_max_tokens` | 4096 | `max_tokens` for the Messages API |
| `ai.openai_image_timeout` | 300 | OpenAI image-generation HTTP timeout (s) — high-quality `gpt-image-1` renders can take minutes |

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
> so large requests wait longer. For Codex, keep concurrency at 1 — `auth.json` is
> single-writer (tokens rotate on refresh).

> **Note (memory):** Odoo applies a soft `RLIMIT_AS` (= `limit_memory_hard`) to its
> workers; the CLI's JS runtime needs far more *virtual* address space than that and
> would abort with a `MemoryExhaustion` assertion. The transport raises the child's
> soft limit back to the (unlimited) hard limit via `preexec_fn`, so the CLI uses the
> host's real RAM. No change to the Odoo worker's own limit.
