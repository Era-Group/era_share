AI Vibe Coding – Security Playbook

# Authentication & Sessions
01 – Session Lifetime. Set session expiration limits. JWT sessions should never exceed 7 days and must use refresh token rotation.
02 – Never use AI-built auth. Use Clerk, Supabase, or Auth0.
03 – Due to chat access, keep API keys strictly secured. Use process.env keys.

# Secure API Development
04 – Rotate secrets every 90 days minimum.
05 – Have the AI verify all suggested packages for security before installing.
06 – Always opt for newer, more secure package versions.
07 – Run npm audit fix after every build.
08 – Sanitize all inputs using parameterized queries always.

# API & Access Control
09 – Enable Row-Level Security in your DB from day one.
10 – Remove all console.log statements before deploying production domain.
11 – Use CORS to restrict access to your allow-listed production domain.
12 – Validate all redirect URLs against an allow-list.
13 – Add auth and rate limiting to every endpoint.

# Data & Infrastructure
14 – Cap AI API costs within your code and dashboard.
15 – Add DDoS protection via Cloudflare or Vercel edge config.
16 – Lock down storage access so users can only use their own files.
17 – Validate upload limits by signature, not by extension.
18 – Verify webhook signatures before processing payment data.

# Other Rules
19 – Review permissions server-side—UI-level checks are not security.
20 – Log critical actions: deletions, role changes, payments, exports.
21 – Build real account deletion flows. Large fines are not fun.
22 – Automate backups then actually test them. An untested backup is useless.
23 – Keep test and production environments fully separate.
24 – Never let webhooks touch real systems in the test environment.

---

# Module Reference — `era_crm_ai_agents_base` (COMPLETE, installed)

> Technical reference for future Claude Code sessions. Documents the base module **as actually built** (Odoo 19, `19.0.4.0.0`, depends: `base`, `mail`, `crm`, **`ai`** (native AI, EE), **`era_ai_accounts`** (Claude CLI/subscription transport), LGPL-3). This is the foundation every one of the 15 agent modules inherits. For build rules / Odoo 19 pitfalls see `era_crm_ai_agents_rules.md`.

## Architecture in one paragraph (FINAL)
We **do not** call any LLM provider ourselves and **do not** add a provider. Every LLM call runs through **Odoo 19 native AI** (`odoo.addons.ai.utils.LLMApiService`, providers **OpenAI + Google only**) **or** through the **Claude CLI / subscription** transport from `era_ai_accounts` (`provider='anthropic_cli'`). No MCP, no Claude/Anthropic API key. Our compliance guarantees are injected by the **AI Compliance Guard** (`services/ai_compliance_guard.py`), which **monkeypatches** the native egress methods and sits **OUTERMOST on the public `request_llm`** (a call-graph guarantee — see the Service section). The legacy `LLMRouter` is **gone**, replaced by the guard.

## Purpose
Pure shared infrastructure — no agent business logic. Provides: an agent registry, a slim pricing **rate card**, the inheritable `crm.ai.agent.mixin`, per-call consumption tracking with a hard limit (Rule 14: **dollar cap on the priced API path, estimated-token limit on the CLI path**), an append-only critical audit log (Rule 20), the human approval gate, and the **AI Compliance Guard** (PDPL consent + PII redaction, env-only keys, audit). Internal model names stay `crm.ai.*` (no `era_` prefix).

## Models (real field names)

### `crm.ai.agent` — agent registry (one row per agent)
`name` (Char, req, translate) · `tech_name` (Char, req, index, **sql-unique**, copy=False — the stable key satellites look up) · `icon` (Binary) · **`transport`** (Selection `api`/`cli`, default `api`, req — `api`=native OpenAI/Google, `cli`=Claude subscription via era_ai_accounts) · **`cli_account_id`** (M2O `era.ai.account`, set null — used when `transport='cli'`) · **`cli_model_code`** (Char, default `opus` — Claude alias/id for the CLI path) · **`model_code`** (Char — native model code for the API path, e.g. `gpt-4.1-mini`) · **`model_code_advanced`** (Char — optional, used for `sensitivity='high'`) · `active` (Bool) · `state` (Selection `enabled`/`paused`/`capped`, default `enabled`) · `last_run` (Datetime, ro) · `currency_id` · `monthly_cost` (Monetary, **compute, NON-STORED** — sums current-month `crm.ai.usage.cost`) · **`monthly_tokens`** (Integer, **compute, NON-STORED** — sums current-month `total_tokens`; the CLI consumption figure). **`default_model_id` was removed** — model selection now lives on the agent (`model_code`/`model_code_advanced` + `cli_model_code`), not in a catalog.
- `action_pause()` / `action_enable()` — manager-facing direct writes.
- `_model_for_sensitivity(sensitivity)` → `(provider, code)` for the API path (`high`→`model_code_advanced` else `model_code`; provider derived from code via `_provider_for_code`: `gemini*`/`google*`→google else openai). Raises if no `model_code`.
- `@api.constrains` `_check_cli_account`: `transport='cli'` requires `cli_account_id`.
- **Controlled sudo writers** (so the no-sudo mixin can update read-only-to-users fields): `_record_run()` (writes only `last_run`), `_mark_state(state)` (writes only `state`), `_get_or_create_agent(tech_name, default_vals=None)` (`@api.model`; sudo find-or-create incl. archived, returns record **rebound to caller env** via `with_env`).

### `crm.ai.model` — pricing **rate card** (NOT a catalog)
Native AI owns the list of available models; this table adds only what native AI does not expose: **per-token pricing** for the dollar cost cap (Rule 14). `name` (req) · `code` (Char, **sql-unique** — the native model id the price applies to, e.g. `gpt-4o`) · `provider` (Selection `openai`/`google`, informational) · `price_input_1k` / `price_output_1k` (Float 12,6, USD per 1K tokens) · `active`. Dropped vs the old catalog: **`tier`, `max_context`, `env_key_param`** (keys are env-only; selection is on the agent). A code with no active row is a Rule-14 fail-safe (blocked by default on the API path, or flagged `unpriced`). The CLI path never consults this card.

### `crm.ai.usage` — per-call consumption + limits (single source of truth)
`agent_id` (M2O, req, cascade) · `model_id` (M2O, set null) · `user_id` (M2O res.users, default uid — **captured before sudo** so the per-user record rule works) · `input_tokens` / `output_tokens` (Int) · `total_tokens` (Int, compute **stored**) · `currency_id` · `cost` (Monetary) · **`unpriced`** (Bool — API model had no rate-card price; cost 0) · **`via_cli`** (Bool — CLI/subscription path; cost 0 **by design**, governed by the token limit) · **`token_source`** (Selection `estimated`/`measured`, default `estimated`) · `create_date` (auto = month boundary).
- **Counts are length-ESTIMATED on BOTH paths** (~1 token / 4 chars): native `request_llm` returns text only, no provider usage at the seam, so `token_source` is `estimated` everywhere today (`measured` reserved for if provider usage is ever threaded through). **Not an accounting figure — an operational budget.**
- `record(agent, model, in_tok, out_tok, cost, unpriced=False, token_source="estimated", via_cli=False)` — `@api.model`, **sudo-creates** one row.
- **Dollar cap (API path):** `_get_cap` (per-agent `…monthly_cost_cap.<tech_name>` → global; sudo config read; `None`→fail-safe) · `_current_month_cost` (sudo sum, all users) · `is_over_cap` (**fails safe**: `None`→block; `≤0`→opt-out; else `cost >= cap`).
- **Token limit (CLI path):** `_get_token_limit` (per-agent `…monthly_token_limit.<tech_name>` → global) · `_current_month_tokens` (sudo sum of `total_tokens`, all users) · `is_over_token_limit` (same fail-safe shape on tokens).
- `_is_over_limit(agent)` — path-aware: token limit for `transport='cli'`, else dollar cap.
- `cron_check_caps()` — daily net using `_is_over_limit`: `enabled`→`capped` when over, `capped`→`enabled` when back under (leaves manually `paused` agents alone). This is the **durable** auto-pause (an attended block's inline mark may roll back with the request; the cron reconciles).

### `crm.ai.audit.log` — append-only critical log (Rule 20)
`event_type` (Selection: `send`, `delete`, `role_change`, `external_contact`, `llm_call_failed`, `cost_cap_exceeded`, `approval_requested`, **`ai_request`** (allowed), **`blocked`**, **`unpriced_model`**, **`compliance_disabled`**, `other`; req) · `agent_id` · `user_id` (default uid) · `model_ref` (**Reference**, `_selection_target_model`, "model,id") · `value_before` / `value_after` (Text) · `create_date`.
- `log(event_type, agent=None, record=None, before=None, after=None)` — `@api.model`, **sudo-creates** (logging must always succeed). `_to_text` json-encodes dict/list.
- **Immutable:** `write()`/`unlink()` raise `UserError` unless `_is_admin()` (`env.su` or `base.group_system`).

### `crm.ai.approval` — human gate
`agent_id` · `proposed_content` (Text, req) · `edited_content` (Text — supersedes proposed) · `effective_content` (Text, compute = edited or proposed) · `record_ref` (Reference) · `reviewer_id` (M2O res.users) · `state` (Selection `pending`/`approved`/`rejected`, default pending) · `decided_on` (Datetime, ro) · `source_model` (Char, ro — model to fire the callback on).
- `create_request(agent, content, record=None, reviewer=None, source_model=None)` — `@api.model`, **no sudo** (runs as caller).
- `action_approve()` → set approved + reviewer + decided_on → audit `other` → `_fire_callback()` → `env[source_model]._ai_on_approved(self)`.
- `action_reject()` → set rejected → audit `other`.

## Mixin — `crm.ai.agent.mixin` (AbstractModel; **no sudo anywhere**)
Each agent sets `_agent_tech_name`. The mixin's only job for an LLM call is to (a) pick the model/transport and (b) **stamp the context the guard needs** — `CTX_AGENT` (=`_crm_ai_agent_tech_name`), `CTX_RECORD` (=`_pdpl_record`), `CTX_UNATTENDED` (=`crm_ai_unattended`). Methods:
- `_call_llm(prompt, sensitivity="low", system=None, record=None, unattended=False, max_output_tokens=1024)` → stamps the context, then **branches on `agent.transport`**:
  - `cli` → binds `cli_account_id` into ctx (`era_ai_account_id`), builds `LLMApiService(env, provider='anthropic_cli')`, model = `cli_model_code or 'opus'`.
  - `api` → `provider, code = agent._model_for_sensitivity(sensitivity)`, builds `LLMApiService(env, provider)`.
  - then `service.request_llm(code, [system] or [], [prompt], temperature=0.2)`. **The guard (patched onto `request_llm`) does consent/redaction/limit/audit/usage** — the mixin no longer records usage or checks the cap itself. **`CTX_AGENT` is the load-bearing flag**: an LLM call that does not go through this method drops to the guard's best-effort path (no consent gate, no hard-block); a tripwire warns if a CLI call arrives without it.
- `_check_cost_cap(agent=None)` — advisory fast-fail: raises if `paused`/`capped` or `crm.ai.usage._is_over_limit(agent)` (path-aware: token vs dollar). Authoritative enforcement is in the guard at call time.
- `_log_critical(event_type, record=None, before=None, after=None)` — delegates to `crm.ai.audit.log.log()`.
- `_request_human_approval(content, record=None)` — `create_request(...)` (reviewer = `record.user_id` if present else current user) + `_log_critical("approval_requested")`; returns the approval.
- `_ai_on_approved(approval)` — **no-op hook**; concrete agents override to perform the real action using `approval.effective_content` / `approval.record_ref`.
- Internal: `_get_agent_record()` (via `_get_or_create_agent`). The old `_record_usage`/`_select_model` are gone (the guard records usage).

## Service — `services/ai_compliance_guard.py` (the single enforcement point)
`install()` (called at import) **monkeypatches** the native `LLMApiService.request_llm` / `.get_embedding` / `.get_transcription` and `ir.actions.server._ai_action_run` (record-capture). Idempotent (sentinel `_era_crm_ai_guard_installed`); captures native signatures for the **mandatory drift test**.
- **Outermost guarantee:** we patch the **public** `request_llm`; native `request_llm`→`_request_llm_silent`→`self._request_llm` reaches the transport (incl. the era_ai_accounts Claude CLI subprocess) **underneath** us. So redaction/consent **always run before any prompt leaves** — a call-graph guarantee, not load-order. (Manifest still depends on `era_ai_accounts` so the guard also patches **last** → outermost on shared methods like `get_embedding`.)
- **Scope gate `_is_our_agent_call`** (= `CTX_AGENT` present): OUR agents → `_text_strict` (FULL: consent gate + record-driven redaction + **unmapped-PII hard-block** + limit + audit + usage); native Ask-AI/ai_fields → `_text_native` (best-effort masking + audit, **no** hard-block, so native features keep working).
- **`is_cli`** = `service.provider == 'anthropic_cli'`. On the strict path it selects the **token limit** (`_enforce_consumption`) and **exempts** the call from the unpriced-model block; usage is recorded `via_cli=True`, cost 0.
- **Rule 03 `_assert_env_only_key`** (GLOBAL, every call): fails closed if a key is set in the native AI UI for `openai`/`google`. `anthropic_cli` has no key → no assertion (clean by construction).
- **Redaction** (`pii_redaction.Redactor`, record-driven): masks exact known values (name split for Arabic, phone-format-tolerant) as `[[PII:KIND:N]]`; a secondary regex net flags unmapped phone/email/national-id (strict path → hard block). `unmask(strict=True)` raises `RedactionError` on a mangled token (response withheld); native path uses `strict=False`.
- **Configurable layers** (`_flag`, all default ON): operational `enable_cost_cap`, `enable_audit`, `block_unpriced_model`; compliance `enable_pii_redaction`, `enable_consent_check` (`_compliance_on` warns on every call while off).
- Cron/unattended (`CTX_UNATTENDED`) blocks **skip-with-audit** (return empty, never raise/send); attended blocks raise `UserError`.

## The approved sudo elevations (exhaustive)
1. **Config read** — `crm.ai.usage._get_cap()` **and `_get_token_limit()`** read a limit from `ir.config_parameter` (sudo read only).
2. **Audit create** — `crm.ai.audit.log.log()` creates one immutable audit row.
3. **Usage aggregate** — `crm.ai.usage._current_month_cost()` **and `_current_month_tokens()`** sum the agent's monthly usage (sudo read/sum only).
4. **Usage record** — `crm.ai.usage.record()` creates one usage row.
5. **Agent controlled writers** — `_record_run()` (last_run), `_mark_state()` (state), `_get_or_create_agent()` (registry lookup/create).

The token-limit helpers (1, 3) are the same narrow read/sum category as their dollar-cap twins — no new elevation kind. Anything beyond these must be flagged and approved — never assumed. The mixin, the guard's record-resolution, and all agent logic run as the calling user (Rule 09/19); only these five categories elevate.

## Security
- **Groups** (categorized via Odoo 19 `res.groups.privilege` → `ir.module.category`): `group_crm_ai_user` ("AI Agents User", salesperson scope) and `group_crm_ai_manager` ("AI Agents Manager", implies user, includes `base.user_admin`).
- **ACL** (`ir.model.access.csv`): user is **read-only** on `crm.ai.agent` / `crm.ai.model` / `crm.ai.usage`; manager has full CRUD. `crm.ai.approval`: user `r/w/create` (no unlink), manager full.
- **Audit-log access exception:** AI **users have NO ACL row** on `crm.ai.audit.log` (no read); manager is **read-only** (`1,0,0,0`); only `base.group_system` gets write/create/unlink — and code still blocks edits/deletes for non-admins.
- **Record rules** (`crm_ai_agent_rules.xml`): `crm.ai.usage` user sees own (`user_id = uid`), manager all; `crm.ai.approval` user sees assigned (`reviewer_id = uid`), manager all.
- **Manager-only protection settings** (`res.config.settings`, fields restricted to `group_crm_ai_manager`): the five layer toggles. Disabling a **compliance** layer (PII redaction / consent) writes a `compliance_disabled` audit row (who/when). **`res.partner`** gains `crm_ai_intl_processing_consent` (Bool) + `crm_ai_consent_date` (the PDPL consent the guard reads).

## Data / UI
- `data/crm_ai_model_data.xml` (seeds the **rate card**: gpt-4.1-mini, gpt-4o, gemini-2.5-flash, gemini-2.5-pro — code+provider+prices) · `data/ir_config_parameter_data.xml` (global **cost cap = 100.0 USD**, global **token limit = 2,000,000**, five layer toggles = True) · `data/ir_cron_data.xml` (daily `cron_check_caps`, runs as `base.user_root`).
- Rate-card list/form/search; usage list (tokens + `token_source` + `via_cli` + cost, filters CLI vs priced) + pivot; **dashboard** (per-agent list showing **both tokens and $** with footer sums — a `$0` CLI agent is never read as free — + graph by state); agent form with transport radio (api↔cli fields toggle); manager Settings page; partner AI&PDPL page. Root menu **CRM AI Agents** → Dashboard / Agents / Approvals (user) and Configuration → Models / Usage / Audit Log / Settings (manager).
- **Migrations:** `19.0.2.0.0` (consent columns pre-create + drop stale catalog rows), `19.0.3.0.0` (drop orphan `tier`/`max_context`/`env_key_param`/`default_model_id`). New `19.0.4.0.0` fields (transport/cli_*, token_source/via_cli) auto-add columns — no migration needed.

## Tests (`tests/`) — 22 methods, all green at 19.0.4.0.0
`test_native_ai_signatures` (drift smoke test — fails loudly if Odoo changes a patched signature) · `test_pii_redaction` (Arabic mask/restore/leftover/mangled) · `test_guard_scope` (strict hard-block vs native pass-through) · `test_guard_toggles` · `test_unpriced` (API unpriced block / allow-flags-usage) · `test_cli_token_path` (**redaction-before-transport safety test** — real Arabic name/phone never reach the transport; CLI→strict; CLI exempt from unpriced block; token-limit block+capped+audit; **tripwire** warns without CTX_AGENT; CLI-account constraint).
- **Anti-bypass (signature drift + tripwire) exists; a dedicated CI test forbidding agent modules from importing `requests`/`openai` or calling native internals is RECOMMENDED but NOT yet built.**

## How to build an agent on top of the base
1. New module `era_crm_ai_agents_<name>`, `depends = ['era_crm_ai_agents_base', ...]`.
2. Add `crm.ai.agent.mixin` to a model (`_inherit = ['some.model', 'crm.ai.agent.mixin']`, e.g. on `crm.lead`, or a new model) and set `_agent_tech_name = 'era_<name>'`.
3. **Pre-seed** the `crm.ai.agent` row on install (post-init hook calling `_get_or_create_agent`, or a data file) so runtime stays read-only for users. Set its `transport` + `model_code` (api) or `cli_account_id`/`cli_model_code` (cli).
4. **Always** call `self._call_llm(prompt, sensitivity='low'|'high', record=<the record>)` for LLM work — never build `LLMApiService` or call a provider/CLI yourself. This stamps `CTX_AGENT` so the guard enforces consent/redaction/limit/audit. Pass `record=` whenever personal data is involved (drives redaction + consent); pass `unattended=True` for cron/batch (block = skip-with-audit, no raise).
5. For sends / Arabic content: `self._request_human_approval(content, record)` and override `_ai_on_approved(approval)` to perform the action from `approval.effective_content` / `approval.record_ref` (human-in-the-loop).
6. Log sensitive decisions with `self._log_critical(event_type, record, before, after)`.
7. Optional per-agent limits: `era_crm_ai_agents.monthly_cost_cap.<tech_name>` (api) or `era_crm_ai_agents.monthly_token_limit.<tech_name>` (cli).
8. If an agent uses a new API model code, **add its price to the rate card** (else the API call is blocked as unpriced). Follow the Odoo 19 verified patterns in `era_crm_ai_agents_rules.md`.