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
- Every agent runs under **real user permissions** (`res.users` / `res.groups`), **not superuser** (Rule 09 / 19): interactive runs use the acting salesperson; scheduled agent runs use the configurable least-privilege cron identity (see "Cron execution identity"). The only superuser path for agent work is the explicit, manager-selected, UI-warned **OdooBot** cron opt-out. (System-maintenance crons run as root by design — see same section.)
- **Log every sensitive decision** (send, delete, role change, external contact, export) to the **critical audit log** (Rule 20).
- **PDPL compliance:** explicit consent before any marketing message; honor opt-out within **72h**; support **DSAR** access/erasure requests; respect Saudi data residency.
- Keep a **human in the loop** for Arabic messages (and for all true "sending" actions, via the approval layer).

---

## No Hardcoded Policy (project-wide, non-negotiable)

**No behavioral value may be hardcoded as a constant in code.** Every rule,
threshold, timing, list, toggle, or limit that a manager or business owner might
reasonably want to change MUST be:

- **Configurable** via a manager-facing `res.config.settings` page
  (`group_crm_ai_manager`), backed by `ir.config_parameter` — or, for editable
  lists, a small manager-editable model.
- **Toggleable** with an enable/disable switch where the rule is optional —
  **toggle OFF = the rule is skipped entirely**, not merely set to a neutral value.
- **Safely defaulted** — defaults preserve the intended behavior (KSA-correct,
  all protections ON), so an untouched install behaves exactly as designed.
- **Documented** — what it does, its default, and where to change it.

**The test:** *"Would a manager / business owner reasonably want to change this?"*
If yes → it is a setting.

**Counts as policy (must be configurable):** timings (working hours, send
windows, prayer-block minutes, opt-out window), thresholds (lead-score cutoffs,
dormancy days, cap limits), feature toggles (weekend no-send, Ramadan quiet, each
protection layer), editable lists (greetings, honorifics, loss-reason buckets),
and any "magic number" encoding a business or cultural policy.

**Does NOT count (leave in code):** genuine technical internals — token-mask
format, monkeypatch seams, API wire formats, ORM field names. Do not over-expose
internals as settings.

**Enforcement:** a hardcoded behavioral constant is a **defect**, flagged by
`/review-logic` under the dimension *"Any hardcoded policy value that should be a
setting?"* — never an acceptable shortcut. Every agent builds configurability in
from the start; none ships policy constants to be retrofitted later. This rule
governs all 16 modules, retroactively including the module-1 compliance engines.

---

## Approved `sudo` Elevations (exhaustive)

The mixin (`crm.ai.agent.mixin`) and all agent business logic run under the calling user — **never** `sudo` (Rule 09 / 19). The ONLY permitted superuser elevations are these narrow, single-purpose helpers. Elevations 1–5 live on the base models; each does exactly one thing; nothing else may ride on them:

1. **Cap read** — `crm.ai.usage._get_cap()` reads the cap from `ir.config_parameter` (sudo read only).
2. **Audit create** — `crm.ai.audit.log.log()` creates one immutable audit row (sudo create only; edits/deletes stay blocked for non-admins).
3. **Usage aggregate** — `crm.ai.usage._current_month_cost()` sums the agent's monthly usage for the cap check (sudo read/sum only).
4. **Usage record** — `crm.ai.usage.record()` creates one usage row (sudo create only).
5. **Agent controlled writers** — `crm.ai.agent._record_run()` (last_run), `_mark_state()` (state), and `_get_or_create_agent()` (registry lookup/create) — each strictly field/purpose-scoped, result rebound to the caller's env.

Module-specific elevations (approved per module, same single-purpose discipline):

6. **Public opt-out controller** (`era_crm_ai_agents_compliance`, module 1) — the public unsubscribe endpoint (`controllers/opt_out_controller.py`) runs `auth='public'` because the opting-out recipient is, by definition, not logged in (PDPL-mandated). It uses one narrow sudo solely to: validate a signed token → resolve the partner → call `process_opt_out` (withdraw marketing consent + clear `crm_ai_intl_processing_consent` + audit). The token is signed with Odoo's instance secret (`database.secret`); no new stored secret. It writes nothing else under sudo. (The 72h enforcement cron runs as `base.user_root`, the normal cron context, not an ad-hoc sudo.)
7. **Compliance config read** (`era_crm_ai_agents_compliance`, module 1) — a read-only helper (`_compliance_config`) sudo-reads ONLY `ir.config_parameter` keys under the `era_crm_ai_agents_compliance.*` namespace, so the send-window / norms / opt-out engines can load manager settings while running as a salesperson. Read-only; writes nothing; touches no other namespace and no secret. Same narrow category as elevation #1 (cap read).
8. **Lead-Gen config read** (`era_crm_ai_agents_lead_gen`, module 16) — the engine's `_cfg` helper (`services/lead_gen_engine.py`) sudo-reads ONLY `ir.config_parameter` keys under the `era_crm_ai_agents_lead_gen.*` namespace (master toggle, targeting, decision-maker toggle, missing-token policy), so the prospecting waterfall can load manager settings while running as a salesperson. Read-only; writes nothing; touches no other namespace and no secret. Same narrow category as elevations #1 (cap read) and #7 (compliance config read). NOTE: source tokens are NOT read this way — they are read straight from the process environment via `os.getenv` (no sudo, never the DB), so this elevation never touches a secret.

_(Elevation #9 "Lead-Gen cost re-attribution" was removed: the per-record split
that needed a `sudo().unlink()` of the guard's usage row was cut in favour of
batch-level cost — one `source_api` usage row per run via the already-approved
usage create (#4), plus the guard's own `llm` row. No unlink remains.)_

**Any new `sudo` elevation must be flagged and approved before use — never assumed.** This list is the registry of what has been approved; extend it (with the same single-purpose discipline) when a new elevation is approved.

---

## Cron execution identity (Rule 09)

There are **two distinct classes of scheduled job** in the suite, and they
intentionally run under **different identities**. This split is a convention, not
an inconsistency:

1. **Agent operations → the configurable resolver (default: least-privilege user).**
   Any cron that performs **agent data processing** (discovery, creation, de-dup,
   enrichment, sending — anything touching business/personal data on behalf of an
   agent) resolves its run-user via the single Base resolver
   `crm.ai.agent._get_cron_run_user()` and applies it with `model.with_user(...)`.
   No agent cron hardcodes a user. Two modes, set in Settings (manager-only,
   `era_crm_ai_agents.cron_run_*`):
   - **`user` (DEFAULT)** — a manager-chosen **internal** user (validated
     `share=false`), seeded to the shared least-privilege **CRM AI Automation**
     account (`base.group_user` + `group_crm_ai_user` only, no password, internal).
     One identity for the whole 16-module suite; a manager may repoint it at any
     existing internal user. This is how **Rule 09 is honored**: the framework
     forces `su=True` ONLY for `SUPERUSER_ID` (`orm/environments.py`), so
     `with_user(<internal user>)` runs with **`su=False`** → ACLs and record rules
     are fully enforced. No `sudo()` is used to run agent logic.
   - **`odoobot`** — the explicit, **warned opt-out**: runs as `base.user_root`
     (uid 1 → `su=True` → bypasses ACLs/record rules). The Settings UI shows a
     visible danger warning (superuser, bypasses access rules, contradicts Rule 09,
     PDPL risk). NOT the default; chosen deliberately and at the manager's risk.
   - **Fail-safe:** a missing/invalid/portal/inactive configured user falls back to
     the seeded automation account, then the calling user — **never silently to
     root.** Reading the two config keys is the same narrow read as elevation #1.

2. **System-maintenance crons → `base.user_root` (by design, outside the resolver).**
   Crons that do **system reconciliation**, not agent data processing, correctly
   run as root and MUST NOT be routed through `_get_cron_run_user()`:
   - `era_crm_ai_agents_base` — `cron_check_caps` (daily cost/token cap
     reconciliation across **all** users' usage).
   - `era_crm_ai_agents_compliance` — the 72h opt-out enforcer and the prayer-time
     cache warmer.
   These need a global, cross-user view and own no agent-specific data decision, so
   the least-privilege agent identity would be both wrong and insufficient. Keeping
   them on root is the intended convention: **root for system maintenance, the
   resolver for agent operations.**

When a future agent module adds a scheduled run, it follows class 1 (call the
resolver; never hardcode a user, never default to root). Only add a class-2
root cron for genuine system reconciliation, and note it here.

---

## External Network Egress Registry (non-LLM)

All LLM traffic flows through Odoo native AI / the Claude CLI behind the base AI
Compliance Guard (see above). Any **direct** outbound network call that is NOT on
that LLM path is forbidden by default and MUST be registered here before use,
stating: caller, endpoint, data sent (PII-free unless explicitly justified),
auth, and fail-safe.

1. **Prayer-times API** (`era_crm_ai_agents_compliance`, module 1) — the
   project's FIRST direct external (non-LLM) egress.
   - **Caller:** `services/send_window.py` prayer-time lookup, through the
     `crm.ai.prayer.cache` layer (live calls only on a cache miss; a daily
     warm-cron pre-fetches).
   - **Endpoint:** Aladhan `GET https://api.aladhan.com/v1/timingsByCity`.
   - **Data sent:** ONLY city + country code + date + calculation method.
     **NEVER** any partner name, phone, email, national ID, or other PII.
   - **Auth:** none — no API key (Rule 03 satisfied by construction). If a
     key-based provider is ever configured, the key is env-only (a system
     `ir.config_parameter`), never stored in the DB.
   - **Fail-safe:** cache-today → live API → last-known-day cached times for that
     city (+ audit warning) → hard block only if nothing exists for the city and
     the API is down (never send blind).
   - **Anti-bypass CI:** when the "no direct egress / no `requests`/`openai`
     import in agent modules" test is built, it MUST allowlist **exactly this one
     call** (this module's prayer-times fetch) and nothing else — narrow scope:
     this endpoint only, no PII, no key.

2. **Lead-Gen source calls** (`era_crm_ai_agents_lead_gen`, module 16) — the
   prospecting engine's outbound source lookups.
   - **Caller:** the SINGLE seam `crm.ai.lead_gen.agent._http_get`
     (`models/crm_ai_lead_gen_agent.py`), reached only via
     `LeadGenEngine.http_get`. `requests` is imported lazily and ONLY there; no
     handler imports it. GET-only today.
   - **Endpoints:** the active source's API, by `provider_type` (handlers under
     `services/handlers/`): web search (SerpAPI `serpapi.com`, Brave
     `api.search.brave.com`, Google CSE `googleapis.com/customsearch`, Bing
     `api.bing.microsoft.com`); business registries (OpenCorporates
     `api.opencorporates.com`, Saudi MoC / Business Center via a
     manager-provisioned env base URL); public-page scrape (a manager-set
     `ERA_LEADGEN_SCRAPE_URL`); contact-data (Hunter `api.hunter.io`; Apollo /
     Lusha / RocketReach / SignalHire deferred — POST). LinkedIn is a flagged
     stub (no call).
   - **Data sent:** ONLY targeting terms (sectors / regions / company size / job
     titles) and the source's own token. **NEVER** a partner's name, phone,
     email, or national ID — Lead Gen DISCOVERS new records, it does not send
     known PII out.
   - **Auth:** each source's API token, read at call time from the env var the
     provider NAMES (`env_key_param`) via `os.getenv` — never stored in the DB
     (Rule 03). A source with no token resolves `token_present=False` and is
     skipped (audited per the warn/silent policy).
   - **No token in logs (Rule 03):** for query-param-token providers (SerpAPI,
     Google CSE) the token rides in the URL, and a network library's exception
     string can echo the full URL. `_http_get` therefore logs ONLY the host
     (`scheme://netloc`, via `_safe_host`) + the exception CLASS name — never the
     full URL, query string, or raw exception — to either the server log or the
     `source_fetch_failed` audit row.
   - **Run context:** the scheduled run executes under the **suite-wide
     configurable cron identity** (see "Cron execution identity" below), which
     defaults to the shared least-privilege **CRM AI Automation** internal user —
     non-superuser, so creation/de-dup obey ACLs (Rule 09). The cron resolves it
     at run time via `crm.ai.agent._get_cron_run_user()` and applies it with
     `model.with_user(...)`; it does NOT hardcode a user. (The old per-module
     "Lead Generation Bot" was retired in `19.0.1.1.0` — a post-migration
     re-points existing crons and removes that user.)
   - **Triple gate (nothing fires by default):** the module master toggle
     (`era_crm_ai_agents_lead_gen.enabled`, default OFF) AND the per-source
     `active` flag (all seeded OFF) AND `token_present`. Decision-maker sources
     need the additional `fetch_decision_makers` toggle (default OFF). Every
     source call books its cost (Rule 14) and the run is audited (Rule 20).
   - **Fail-safe:** any network error / empty result is isolated — the waterfall
     falls through to the next source and the run survives; a source is never
     retried blindly and never fakes a result.
   - **Anti-bypass CI:** when the "no direct egress / no `requests`/`openai`
     import in agent modules" test is built, it MUST allowlist EXACTLY this one
     seam (`crm.ai.lead_gen.agent._http_get`) for this module — no handler may
     import `requests` directly.

Any further direct egress must be added here AND allowlisted in the anti-bypass
test before use.

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
- **Mirror on `res.users`:** the groups M2M on a USER is now **`group_ids`**, not `groups_id` (`<field name="group_ids" eval="[Command.set([ref('base.group_user'), ...])]"/>`). `groups_id` raises `ValueError: Invalid field 'groups_id' in 'res.users'` and aborts the whole registry load. `Command` IS available in the XML eval context (core `base/data/res_users_data.xml` uses `Command.set`). Verified building the Lead-Gen Bot cron user (module 16).

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

### 6. `ir.actions.act_window` target
- ❌ Wrong: `<field name="target">inline</field>` → `ValueError: Wrong value for ir.actions.act_window.target: 'inline'` (registry fails to load).
- ✅ Correct: use `current` (open in the main content area) for a settings/config action, or `new` for a dialog. `inline` was removed from the target Selection in 19.
- Note: to open a `res.config.settings` page from a menu, use `target="current"`. Verified building `era_crm_ai_agents_compliance` Compliance Settings.

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
