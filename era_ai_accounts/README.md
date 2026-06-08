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
- **API-key accounts** — OpenAI, Google Gemini, Anthropic (Messages API), and any
  OpenAI-compatible custom endpoint, with secrets stored **encrypted** and
  restricted to *AI Account Managers*.
- **Shared vs personal accounts** — one account for everyone or per-user accounts,
  with `owner` + `allowed users` + record rules controlling who may use each.
- **Dynamic model catalog** — synced from each account (curated set for the CLI
  proxy; live `/models` for key accounts).

## Configure

1. **AI ▸ AI Accounts ▸ New** (managers only).
2. Pick a **provider** and **auth mode**:
   - *Local CLI proxy* (Anthropic): optionally set the CLI binary path / `HOME`;
     no key needed.
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
| `ai.http_timeout` | 120 | Anthropic HTTP timeout (s) |
| `ai.anthropic_max_tokens` | 4096 | `max_tokens` for the Messages API |

> **Note (memory):** Odoo applies a soft `RLIMIT_AS` (= `limit_memory_hard`) to its
> workers; the CLI's JS runtime needs far more *virtual* address space than that and
> would abort with a `MemoryExhaustion` assertion. The transport raises the child's
> soft limit back to the (unlimited) hard limit via `preexec_fn`, so the CLI uses the
> host's real RAM. No change to the Odoo worker's own limit.
