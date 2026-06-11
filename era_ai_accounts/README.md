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
   - *Local CLI proxy* (Anthropic): optionally set the CLI binary path / `HOME`;
     no key needed. **Or click "Login with Claude"** to link a Claude
     subscription in-app (OAuth, manual copy-code flow): the credentials are
     stored once on the server — in an isolated directory, never touching the
     server's own `~/.claude` login — and **every user** runs through that one
     account. *Re-link* / *Disconnect* buttons manage it; managers only.
   - *API key*: paste the provider key (write-only, stored encrypted). For a
     *custom* provider also set base URL / auth header.
3. **Validate connection**, then **Sync models**.
4. Set **scope** (shared/personal), **owner**, and **allowed users**.
5. On an **AI Agent**, set **AI Account** + **Account Model**. Responses are then
   generated through the account.

## Compliance note

Using a Pro/Max **subscription** through the local CLI to serve many Odoo users
draws on that subscription's rate limits and intended-use terms. The per-account
**concurrency cap** and **kill switch** keep this controllable. Replaying the
CLI's OAuth token directly against `api.anthropic.com` is **not** done — it is
blocked by Anthropic and violates the ToS.

## Limitations (v1)

- The CLI proxy supports **chat / RAG answers** only — Odoo's tool-calling
  "Ask-AI navigation" tools are not bridged through the CLI (use an API-key
  account for tool-using agents).
- Claude has no embeddings: for agents with **knowledge sources**, keep the
  agent's *LLM Model* on OpenAI/Gemini (used only for embeddings); generation
  still goes through the account.
- Claude has **no image generation**: the CLI proxy produces text only. For
  blog/article cover images, create a **Cloudflare** or **OpenAI** (`api_key`)
  account and sync its models — `era_seo_suite`'s *Blog Gen* tab then lets you
  pick that account (and a specific image model) directly. Use the account's
  **Note** field to record what it is linked for.
- `codex` / `gemini` CLIs are not installed here, so OpenAI/Gemini use API keys.

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

All of the `ai.cli_*` settings above are editable in the UI: **Settings ▸ AI ▸ "Claude
CLI rate protection"** (no redeploy needed).

> **Throttling (gentle on the connected account):** CLI-proxy calls are throttled by a
> host-wide cross-process semaphore of `ai.cli_max_concurrency` slots (default **1** = at
> most one call at a time across every Odoo worker and user; raise it to allow controlled
> concurrency). Lock files live under `<data_dir>/era_ai_cli_proxy.<n>.lock` and auto-release
> if a worker dies. When `ai.cli_gap_enabled` is on, consecutive calls are also separated by a
> gap that **scales with the request body size** (`min_gap + gap_per_kb × KB`, capped at
> `max_gap`), so large requests wait longer.

> **Note (memory):** Odoo applies a soft `RLIMIT_AS` (= `limit_memory_hard`) to its
> workers; the CLI's JS runtime needs far more *virtual* address space than that and
> would abort with a `MemoryExhaustion` assertion. The transport raises the child's
> soft limit back to the (unlimited) hard limit via `preexec_fn`, so the CLI uses the
> host's real RAM. No change to the Odoo worker's own limit.
