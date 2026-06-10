# Era Group — CRM AI Agents — Project Rules (Claude Code Reference)

> Permanent reference for every Claude Code session on this project.
> Read this file at the start of every session before doing any work.
>
> **Notion source of truth — "CRM AI Agents — Tasks":**
> https://app.notion.com/p/0234c7b3156244a68af638fdc45b7904?v=374165ed6d6a80c19fa2000cc66b2b3d

---

## Project Overview

- This is **NOT a single module**. It is a **SERIES of 16 separate, independent Odoo 19 modules** built **sequentially, one fully completed module at a time**: one shared base module (`era_crm_ai_agents_base`) + 15 isolated agent modules.
- **Agents are isolated.** They communicate **only through shared Odoo data** (e.g. `crm.lead`). Rare direct exchanges (e.g. message approval) go through the **base approval layer** (`crm.ai.approval`).
- The full ordered task plan lives in the Notion database **"CRM AI Agents — Tasks"** (link above). Each task moves through four statuses: **Draft → Development → Test → Live**.
- A module is **"done"** only when **all its tasks reach `Live`** and it **installs/tests cleanly**. Only then do we start the next module. We never work on two modules at once, and we never jump ahead.

---

## Sequential Build Order (one module at a time, in this order)

| Order | Module | Technical name | Depends on |
|------:|--------|----------------|------------|
| 0 | Base | `era_crm_ai_agents_base` | — (foundation; must be finished before ANY agent) |
| 1 | Compliance | `era_crm_ai_agents_compliance` | base |
| 2 | Enrichment | `era_crm_ai_agents_enrichment` | base |
| 3 | Dead-Lead | `era_crm_ai_agents_dead_lead` | base + compliance |
| 4 | Dormant Gold | `era_crm_ai_agents_dormant_gold` | base (+ enrichment) |
| 5 | Lead Score | `era_crm_ai_agents_lead_score` | base |
| 6 | Inbound Qualifier | `era_crm_ai_agents_inbound_qualifier` | base + compliance |
| 7 | Daily Actions | `era_crm_ai_agents_daily_actions` | base + lead_score |
| 8 | Deal Watchdog | `era_crm_ai_agents_deal_watchdog` | base |
| 9 | Conversation Intel | `era_crm_ai_agents_conversation_intel` | base |
| 10 | Account Brief | `era_crm_ai_agents_account_brief` | base |
| 11 | Action WhatsApp | `era_crm_ai_agents_action_whatsapp` | base + compliance |
| 12 | Proposal Gen | `era_crm_ai_agents_proposal_gen` | base |
| 13 | Compose Quality | `era_crm_ai_agents_compose_quality` | base |
| 14 | Arabic Content | `era_crm_ai_agents_arabic_content` | base |
| 15 | Roleplay Trainer | `era_crm_ai_agents_roleplay_trainer` | base |

**Rule:** Do **NOT** start a module until the previous one is fully complete and tested. The **Base must be finished before ANY agent**. Each agent declares its dependencies (base, and sometimes compliance / enrichment / lead_score) in its manifest `depends`.

### The Base module — the six shared "bricks"
Every agent inherits from the Base. The Base contains **no agent-specific business logic** — only shared infrastructure:

1. **Agent registry** — `crm.ai.agent` (one record per agent: identity, default model, activation state, rolling cost counters).
2. **Model catalog + router** — `crm.ai.model` + `LLMRouter` (selects cheap vs advanced model by task sensitivity, builds the request, calls the provider, returns text + token counts + cost).
3. **Abstract mixin** — `crm.ai.agent.mixin` (the heart of the architecture; see below).
4. **Cost tracking + cap** — `crm.ai.usage` (per-call cost, monthly aggregation, auto-pauses an agent when the cap is exceeded — Rule 14).
5. **Critical audit log** — `crm.ai.audit.log` (Rule 20).
6. **Human approval layer** — `crm.ai.approval`.

### The mixin is the heart of the architecture
`crm.ai.agent.mixin` is an `AbstractModel` every agent inherits to get:
- `_call_llm(prompt, sensitivity, **kw)` — checks the cap, calls the router, records usage.
- `_check_cost_cap()` — raises/pauses if the monthly cap is exceeded.
- `_log_critical(event_type, record, before, after)` — writes to the audit log.
- `_request_human_approval(content, record) -> crm.ai.approval` — opens the human gate.
- Class attribute `_agent_tech_name` set by each agent.

### The Compliance layer is a cross-cutting guard, not a standalone agent
Every **sending** agent calls `guard()` **before any message goes out**. `guard()` checks explicit PDPL consent, allowed send windows (prayer-time / Hijri / Ramadan-aware), and cultural discourse norms; it then permits or blocks and logs the decision to the audit log.

---

## AI Transport, Compliance Guard & Consumption Control (FINAL architecture)

> This section reflects the final, implemented architecture and **supersedes any earlier "LLMRouter builds the request and calls the provider" wording in the bricks/mixin above.** We add **no** provider and call **no** provider ourselves.

**All LLM work runs through Odoo 19 native AI** (`odoo.addons.ai`, OpenAI/Google), **or** through the **Claude CLI / subscription** transport from `era_ai_accounts`. No MCP, no Claude/Anthropic API key.

**AI Compliance Guard — the single enforcement point.** `services/ai_compliance_guard.py` monkeypatches the native `LLMApiService` (`request_llm`, `get_embedding`, `get_transcription`) + `ir.actions.server._ai_action_run`. It sits **OUTERMOST on the public `request_llm` — a call-graph guarantee, not a load-order one**: the public method we patch is what every caller hits, and native `request_llm` only reaches the transport (incl. the Claude CLI subprocess) through the private `_request_llm` underneath us. Therefore **PII redaction + PDPL consent always run before any prompt leaves**, including before the CLI subprocess. A mandatory signature smoke test fails loudly if Odoo changes a patched signature on upgrade.

**Claude CLI / subscription transport (`era_ai_accounts`) — adopted.** An agent may set `transport='cli'` to generate through the editor's Claude subscription via the `era_ai_accounts` local CLI proxy instead of the priced OpenAI/Google API. The four compliance guarantees are unchanged (guard outermost, above).

**HARD RULE — `CTX_AGENT`:** every LLM call (CLI **or** API) MUST go through `crm.ai.agent.mixin._call_llm`, which stamps `CTX_AGENT` (+ `CTX_RECORD`). That flag is what makes the guard run FULL enforcement (consent gate + record-driven redaction + unmapped-PII hard-block). An agent that reaches the CLI/LLM any other way **silently drops to best-effort** (no consent gate, no hard-block); a **tripwire warning** fires if a CLI call ever arrives without `CTX_AGENT`.

**Consumption control (Rule 14) is path-specific:**
- **Priced API path** (OpenAI/Google) → **dollar cost cap** (`era_crm_ai_agents.monthly_cost_cap[.<tech_name>]`).
- **CLI/subscription path** → the CLI returns no cost, so a dollar cap is impossible; it is governed by a per-agent **monthly estimated-token limit** (`era_crm_ai_agents.monthly_token_limit[.<tech_name>]`), same fail-safe shape as the cost cap (undeterminable → block; `≤0` → explicit opt-out). The CLI path is **exempt from the unpriced-model rate-card block** and governed by the token limit instead.

**Accuracy of counts — do NOT overstate:** token/cost counts at our seam are length-**ESTIMATED on BOTH the CLI and the priced API path**, because the native `request_llm` returns **text only** (no provider usage at the `request_llm` seam). They are an **operational budget, not an accounting figure** — a future reader must not believe the dollar cap is precise. The `token_source` field is `estimated` everywhere today; `measured` is reserved for if we ever thread real provider usage through. The dashboard shows tokens **and** $ side by side so a `$0` CLI agent is never read as free/unlimited.

**Rule 03 on the CLI path — clean by construction:** the CLI authenticates via the subscription/OAuth through the `claude` binary — **no API key, therefore no DB-stored secret** in our path or in a `cli_proxy` account. (Env-only keys remain mandatory and non-toggleable for the API path; the guard fails closed if a key is pasted into the native AI UI.)

**Accepted risk (ToS):** serving multiple Odoo users through one Claude subscription draws on that subscription's intended-use terms; this is a **knowingly accepted operational risk**, mitigated by `era_ai_accounts`' host-wide concurrency cap + kill switch.

**Configurable protection layers:** four toggles, all default ON. Operational (cost cap, audit) toggle freely; compliance (PII redaction, consent) are **manager-only, audited on disable, and warn on every call while off**. The env-only-key assertion (Rule 03) is never a toggle.

---

## Non-Negotiable Global Rules

- Build **ONLY** in `/opt/odoo/addons/`. **Never** touch `ce/addons`, `ee`, `themes`, `waha`, or `odoo.conf`.
- Secrets (API keys/tokens) live in **environment variables**, read via `ir.config_parameter`. **NEVER** store secrets in code or the database.
- Enforce a **hard AI cost cap in code**, not only in dashboards (Rule 14).
- Every agent runs under the **salesperson's permissions** (`res.users` / `res.groups`), **never superuser** (Rule 09 / 19).
- **Log every sensitive decision** (send, delete, role change, external contact, export) to the **critical audit log** (Rule 20).
- **PDPL compliance:** explicit consent before any marketing message; honor opt-out within **72h**; support **DSAR** access/erasure requests; respect Saudi data residency.
- Keep a **human in the loop** for Arabic messages (and for all true "sending" actions, via the approval layer).

---

## Approved `sudo` Elevations (exhaustive)

The mixin (`crm.ai.agent.mixin`) and all agent business logic run under the calling user — **never** `sudo` (Rule 09 / 19). The ONLY permitted superuser elevations are these five narrow, single-purpose helpers on the base models. Each does exactly one thing; nothing else may ride on them:

1. **Cap read** — `crm.ai.usage._get_cap()` reads the cap from `ir.config_parameter` (sudo read only).
2. **Audit create** — `crm.ai.audit.log.log()` creates one immutable audit row (sudo create only; edits/deletes stay blocked for non-admins).
3. **Usage aggregate** — `crm.ai.usage._current_month_cost()` sums the agent's monthly usage for the cap check (sudo read/sum only).
4. **Usage record** — `crm.ai.usage.record()` creates one usage row (sudo create only).
5. **Agent controlled writers** — `crm.ai.agent._record_run()` (last_run), `_mark_state()` (state), and `_get_or_create_agent()` (registry lookup/create) — each strictly field/purpose-scoped, result rebound to the caller's env.

**This list is exhaustive. Any new `sudo` elevation must be flagged and approved before use — never assumed.**

---

## Odoo 19 Conventions

- Module technical names use **underscores only** and carry the **`era_`** prefix (e.g. `era_crm_ai_agents_base`), **never hyphens**.
- Manifest version format: **`19.0.x.y.z`**.
- Service classes go under a **`services/`** folder.
- Use **new-style model definitions**.
- Standard module layout: `models/`, `views/`, `services/`, `security/`, `data/`, `tests/`, `__manifest__.py`, `__init__.py`.

---

## Odoo 19 — Verified Patterns & Known Pitfalls

> **This project targets Odoo 19.** Odoo 19 changed several ORM/view/security constructs from earlier versions. **Before writing ANY security XML, view XML, or model definition in a new agent module, consult this section first.** If a construct is not covered here and you are unsure, verify it against core source in `/opt/odoo/ce/addons/` (e.g. `sales_team`, `crm`) — **never assume older-version syntax.** When you discover a new Odoo 19 difference, **add it to this section immediately.**

### 1. `res.groups` categorization
- ❌ Wrong: `<field name="category_id" ref="..."/>` on a `res.groups` record.
- ✅ Correct: groups link to a `res.groups.privilege` via `<field name="privilege_id" ref="privilege_x"/>`; the **privilege** carries `category_id` → `ir.module.category`. Chain: `ir.module.category` → `res.groups.privilege` (`category_id`) → `res.groups` (`privilege_id`).
- Note: `res.groups.category_id` was removed in 19. Verified in `sales_team/security/sales_team_security.xml`.

### 2. `res.groups` user assignment
- ❌ Wrong: `<field name="users" eval="[(4, ref('base.user_admin'))]"/>`.
- ✅ Correct: `<field name="user_ids" eval="[(4, ref('base.user_admin'))]"/>`.
- Note: the `users` field/alias is gone; the M2M is now `user_ids`. Same `Invalid field` error class as #1. (Uniqueness is now `UNIQUE(privilege_id, name)`.)

### 3. Search-view group-by
- ❌ Wrong: `<group expand="0" string="Group By">` inside `<search>` → `Invalid view ... search definition`.
- ✅ Correct: bare `<group>` wrapping `<filter ... context="{'group_by': 'x'}"/>`.
- Note: a search `<group>` no longer accepts `expand`/`string` (per `base/rng/common.rng`). Verified in `crm/views/crm_lead_views.xml`. `<group string="...">` is **still valid in form views** (different RNG) — only search-view groups are affected.

### 4. List views
- ❌ Wrong: `<tree>...</tree>`, and `view_mode="tree,form"`.
- ✅ Correct: `<list>...</list>`, and `view_mode="list,form"`.
- Note: `<tree>` was renamed to `<list>` (Odoo 17+) and remains so in 19. Used throughout the base from the start — do the same in every agent.

### 5. Persistent dev server (cross-ref "Dev Environment Notes" below)
- ❌ Wrong: assuming `-u <module>` alone picks up new client actions / assets / crons / menus.
- ✅ Correct: full server **RESTART** (not just `-u`) **+ browser hard-refresh** (Ctrl+F5) after those changes.
- Note: new `ir.cron` records especially need a restart to register. See "Dev Environment Notes".

---

## Dev Environment Notes

- The Odoo server runs as a **persistent process**: after changing Python code or the manifest, **restart the server** (and upgrade the module) for changes to take effect.
- After view / asset / template changes, **hard-refresh the browser** (Ctrl+F5 / Cmd+Shift+R) to clear cached assets.
- Reminder: build only in `/opt/odoo/addons/`; never touch `ce/addons`, `ee`, `themes`, `waha`, or `odoo.conf`.

---

## Module Naming Convention

- Module technical names carry the **`era_`** prefix:
  - Base: `era_crm_ai_agents_base`
  - Agents: `era_crm_ai_agents_<name>` (e.g. `era_crm_ai_agents_compliance`, `era_crm_ai_agents_dead_lead`).
- **Internal Odoo model names do NOT carry the prefix** — they stay `crm.ai.*` (e.g. `crm.ai.agent`, `crm.ai.agent.mixin`, `crm.ai.usage`, `crm.ai.audit.log`, `crm.ai.approval`).

---

## Workflow Instructions for Claude Code

- We proceed **strictly module-by-module, task-by-task, in Module Order**. **Never work ahead.**
- **Before starting a module**, read its **"X.00 — Overview"** task in Notion and fully understand it before writing any code.
- **Within a module**, do the tasks in their numbered order (X.0, X.1, X.2, …).
- **For each task:**
  1. Implement it.
  2. Verify its **Acceptance criteria** from the Notion task.
  3. Update that task's **Status** in Notion: Draft → Development → Test → Live.
- **After finishing a module, confirm with the user before moving to the next module.**
- The shared mixin **`crm.ai.agent.mixin`** is the heart of the architecture; every agent inherits it to get LLM calling, cost-cap checks, audit logging, and approval requests.
