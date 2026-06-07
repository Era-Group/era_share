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

> Technical reference for future Claude Code sessions. Documents the base module **as actually built** (Odoo 19, `19.0.1.0.0`, depends: `base`, `mail`, `crm`, LGPL-3). This is the foundation every one of the 15 agent modules inherits. For build rules / Odoo 19 pitfalls see `era_crm_ai_agents_rules.md`.

## Purpose
Pure shared infrastructure — no agent business logic. Provides: an agent registry, an LLM model catalog + router, the inheritable `crm.ai.agent.mixin`, per-call cost tracking with a hard monthly cost cap (Rule 14), an append-only critical audit log (Rule 20), and a human approval gate. Internal model names stay `crm.ai.*` (no `era_` prefix).

## Models (real field names)

### `crm.ai.agent` — agent registry (one row per agent)
`name` (Char, req, translate) · `tech_name` (Char, req, index, **sql-unique**, copy=False — the stable key satellites look up) · `icon` (Binary) · `default_model_id` (M2O `crm.ai.model`, ondelete set null) · `active` (Bool) · `state` (Selection `enabled`/`paused`/`capped`, default `enabled`) · `last_run` (Datetime, ro) · `currency_id` (M2O res.currency) · `monthly_cost` (Monetary, **compute, NON-STORED** — sums current-month `crm.ai.usage`).
- `action_pause()` / `action_enable()` — manager-facing direct writes.
- **Controlled sudo writers** (so the no-sudo mixin can update read-only-to-users fields): `_record_run()` (writes only `last_run`), `_mark_state(state)` (writes only `state`), `_get_or_create_agent(tech_name, default_vals=None)` (`@api.model`; sudo find-or-create incl. archived, returns record **rebound to caller env** via `with_env`).

### `crm.ai.model` — LLM catalog
`name` (req) · `code` (Char, **sql-unique** — provider model id, e.g. `claude-opus-4-8`) · `provider` (Selection `anthropic`/`openai`/`allam`/`local`, req) · `tier` (Selection `cheap`/`advanced`, req, default cheap) · `price_input_1k` / `price_output_1k` (Float 12,6, USD per 1K tokens) · `max_context` (Int) · `env_key_param` (Char — **the NAME of the `ir.config_parameter` holding the API key, never the secret**) · `active` (Bool). `_compute_display_name` → "Name (Provider)".

### `crm.ai.usage` — per-call cost + cap (single source of truth)
`agent_id` (M2O, req, cascade) · `model_id` (M2O, set null) · `user_id` (M2O res.users, default uid — **captured before sudo** so the per-user record rule works) · `input_tokens` / `output_tokens` (Int) · `total_tokens` (Int, compute **stored**) · `currency_id` · `cost` (Monetary) · `create_date` (auto = month boundary).
- `record(agent, model, in_tok, out_tok, cost)` — `@api.model`, **sudo-creates** one row (users are read-only here).
- `_get_cap(agent)` — per-agent param `era_crm_ai_agents.monthly_cost_cap.<tech_name>` else global `era_crm_ai_agents.monthly_cost_cap`; sudo config read; returns `None` if missing/unparseable.
- `_current_month_cost(agent)` — sudo sum across **all users** (cap must see the true total, not the caller's slice).
- `is_over_cap(agent)` — **fails safe**: `None` cap → `True` (block); cap ≤ 0 → explicit opt-out (`False`); else `current_month_cost >= cap`.
- `cron_check_caps()` — daily net: `enabled`→`capped` when over, `capped`→`enabled` when back under (leaves manually `paused` agents alone).

### `crm.ai.audit.log` — append-only critical log (Rule 20)
`event_type` (Selection: `send`, `delete`, `role_change`, `external_contact`, `llm_call_failed`, `cost_cap_exceeded`, `approval_requested`, `other`; req) · `agent_id` · `user_id` (default uid) · `model_ref` (**Reference**, `_selection_target_model`, "model,id") · `value_before` / `value_after` (Text) · `create_date`.
- `log(event_type, agent=None, record=None, before=None, after=None)` — `@api.model`, **sudo-creates** (logging must always succeed). `_to_text` json-encodes dict/list.
- **Immutable:** `write()`/`unlink()` raise `UserError` unless `_is_admin()` (`env.su` or `base.group_system`).

### `crm.ai.approval` — human gate
`agent_id` · `proposed_content` (Text, req) · `edited_content` (Text — supersedes proposed) · `effective_content` (Text, compute = edited or proposed) · `record_ref` (Reference) · `reviewer_id` (M2O res.users) · `state` (Selection `pending`/`approved`/`rejected`, default pending) · `decided_on` (Datetime, ro) · `source_model` (Char, ro — model to fire the callback on).
- `create_request(agent, content, record=None, reviewer=None, source_model=None)` — `@api.model`, **no sudo** (runs as caller).
- `action_approve()` → set approved + reviewer + decided_on → audit `other` → `_fire_callback()` → `env[source_model]._ai_on_approved(self)`.
- `action_reject()` → set rejected → audit `other`.

## Mixin — `crm.ai.agent.mixin` (AbstractModel; **no sudo anywhere**)
Each agent sets `_agent_tech_name`. Methods:
- `_call_llm(prompt, sensitivity="low", **kw)` → strict order: **(1)** `_check_cost_cap` (before any spend) → **(2)** `LLMRouter(self.env).call(...)` (on `LLMRouterError`: `_log_critical("llm_call_failed", ...)` then re-raise) → **(3)** `_record_usage` + `agent._record_run()`. Returns the router dict.
- `_check_cost_cap(agent=None)` — raises if agent `paused`/`capped`; if `crm.ai.usage.is_over_cap(agent)` → `agent._mark_state("capped")` + `_log_critical("cost_cap_exceeded")` + raise `UserError`.
- `_log_critical(event_type, record=None, before=None, after=None)` — delegates to `crm.ai.audit.log.log()`.
- `_request_human_approval(content, record=None)` — `create_request(...)` (reviewer = `record.user_id` if present else current user) + `_log_critical("approval_requested")`; returns the approval.
- `_ai_on_approved(approval)` — **no-op hook**; concrete agents override to perform the real action using `approval.effective_content` / `approval.record_ref`.
- Internals: `_get_agent_record()` (via `_get_or_create_agent`), `_record_usage(agent, result)` (maps `result['model']` code → `crm.ai.model` → `usage.record()`).

## Service — `services/llm_router.py`
`LLMRouter(env).call(prompt, sensitivity="low", system=None, max_tokens=1024)` → `{text, input_tokens, output_tokens, cost, model}` (`model` = the catalog `code`).
- `_select_model`: `high`→advanced else cheap; prefer `anthropic`, else any active model in tier; raises `LLMRouterError` if none.
- `_read_token(env_key_param)`: `ir.config_parameter` (sudo `get_param`) → OS-env fallback (raw name + UPPER_SNAKE) → raise. **Token is never stored, logged, returned, or put in an exception** (Rule 03).
- Endpoints overridable per-deployment (`era_crm_ai_agents.<provider>_base_url`); anthropic/openai have defaults, allam/local must be configured. Timeout `era_crm_ai_agents.llm_timeout` (default 180s).
- `_call_anthropic` (`/v1/messages`, `x-api-key`, `anthropic-version 2023-06-01`) and `_call_openai_compatible` (Bearer, chat completions — used for openai/allam/local). `_post` funnels all `requests` errors into `LLMRouterError` (no headers/token in message).
- The router **does NOT** enforce the cap or record usage — that is the mixin's job.

## The FIVE approved sudo elevations (exhaustive)
1. `crm.ai.usage._get_cap()` — cap read from `ir.config_parameter`.
2. `crm.ai.audit.log.log()` — create one immutable audit row.
3. `crm.ai.usage._current_month_cost()` — usage aggregate for the cap.
4. `crm.ai.usage.record()` — create one usage row.
5. `crm.ai.agent` controlled writers — `_record_run()` (last_run), `_mark_state()` (state), `_get_or_create_agent()` (registry lookup/create).

Anything beyond these must be flagged and approved — never assumed. The mixin and all agent logic run as the calling user (Rule 09/19).

## Security
- **Groups** (categorized via Odoo 19 `res.groups.privilege` → `ir.module.category`): `group_crm_ai_user` ("AI Agents User", salesperson scope) and `group_crm_ai_manager` ("AI Agents Manager", implies user, includes `base.user_admin`).
- **ACL** (`ir.model.access.csv`): user is **read-only** on `crm.ai.agent` / `crm.ai.model` / `crm.ai.usage`; manager has full CRUD. `crm.ai.approval`: user `r/w/create` (no unlink), manager full.
- **Audit-log access exception:** AI **users have NO ACL row** on `crm.ai.audit.log` (no read); manager is **read-only** (`1,0,0,0`); only `base.group_system` gets write/create/unlink — and code still blocks edits/deletes for non-admins.
- **Record rules** (`crm_ai_agent_rules.xml`): `crm.ai.usage` user sees own (`user_id = uid`), manager all; `crm.ai.approval` user sees assigned (`reviewer_id = uid`), manager all.

## Data / UI
- `data/crm_ai_model_data.xml` (seeds catalog) · `data/ir_config_parameter_data.xml` (global cap = 100.0) · `data/ir_cron_data.xml` (daily `cron_check_caps`, runs as `base.user_root`).
- Per-model list/form/search views; usage pivot; dashboard (list with footer-sum total spend + graph of agents-by-state). Root menu **CRM AI Agents** → Dashboard / Agents / Approvals (user) and Configuration → Models / Usage / Audit Log (manager).

## How to build an agent on top of the base
1. New module `era_crm_ai_agents_<name>`, `depends = ['era_crm_ai_agents_base', ...]`.
2. Add `crm.ai.agent.mixin` to a model (`_inherit = ['some.model', 'crm.ai.agent.mixin']`, e.g. on `crm.lead`, or a new model) and set `_agent_tech_name = 'era_<name>'`.
3. **Pre-seed** the `crm.ai.agent` row on install (post-init hook calling `_get_or_create_agent`, or a data file) so runtime stays read-only for users.
4. Call `self._call_llm(prompt, sensitivity='low'|'high')` for any LLM work — it enforces the cap and records usage automatically.
5. For sends / Arabic content: `self._request_human_approval(content, record)` and override `_ai_on_approved(approval)` to perform the action from `approval.effective_content` / `approval.record_ref` (human-in-the-loop).
6. Log sensitive decisions with `self._log_critical(event_type, record, before, after)`.
7. Optional per-agent cap: set `ir.config_parameter` `era_crm_ai_agents.monthly_cost_cap.<tech_name>`.
8. Seed any new catalog models in the agent's own data; follow the Odoo 19 verified patterns in `era_crm_ai_agents_rules.md`.