# era_customer_success — Developer Handoff

> Self-contained guide to continue development of this module elsewhere. Captures architecture,
> the AI layer, deployment workflow, hard-won Odoo 19 gotchas, and known infra issues.
> Companion files: [CHANGELOG.md](../CHANGELOG.md), Arabic docs in this `doc/` folder
> (FEATURES_AR, USER_GUIDE_AR, IMPROVEMENTS_AR).

Last updated at **v19.0.29.0.0** (2026-07-24).

---

## 1. What it is

A **Customer Success platform** for ERA (Saudi company) on **Odoo 19 Enterprise**, implementing the
company's "خطة نجاح العملاء". It is a **thin orchestration layer** over existing apps — NOT a rebuild.

Reuses: `helpdesk`, `voip`, `whatsapp` (native), `sale_subscription`,
`account_followup`, `survey`, `crm`, `project`, `calendar`, `mail`, and the EE **`ai`** module.

**Core idea:** one `cs.account` per customer, assigned to a CSM engineer, with a 0–100 health score,
a 30-60-90 lifecycle, a unified 360° timeline, a manager KPI dashboard, and a large opt-in **AI layer**
(forecasting, generative drafting, weekly worklists, copilot).

---

## 2. Environment & deployment

| | |
|---|---|
| Code root | `/opt/odoo/addons/era_customer_success` |
| Odoo CE | `/opt/odoo/ce` · EE `/opt/odoo/ee` · ERA submodules `/opt/odoo/submodules/era_share_latest` |
| Python venv | `/opt/odoo/venv/bin/python` |
| Config | `/opt/odoo/odoo.conf` (workers=4, max_cron_threads=2, logfile=`/var/log/odoo/odoo.log`, log_level=warn) |
| **PROD DB** | `ae3229b2-5291-4967-80e1-6368dfecfaae` (live; Arabic locale `ar_AE` active) |
| Sandbox DB | `test_cs_validate` (throwaway; install + smoke-test here first) |
| Git | pushed to `origin/stage` (remote redirects to Era-Group/help19) |

### Validate → deploy loop (used for every change)
```bash
# 1. static checks
python -m py_compile $(find . -name "*.py")
python -c "from lxml import etree, glob; [etree.parse(f) for f in glob.glob('**/*.xml',recursive=True)]"

# 2. upgrade the SANDBOX (catches view/ACL/field errors), then smoke-test logic via shell
odoo-bin -c odoo.conf -d test_cs_validate -u era_customer_success --stop-after-init --no-http
odoo-bin shell -c odoo.conf -d test_cs_validate --no-http   # run ORM smoke tests

# 3. deploy to PROD (workers auto-reload via registry signaling)
odoo-bin -c odoo.conf -d <PROD> -u era_customer_success --i18n-overwrite --stop-after-init --no-http
```
- Use `--i18n-overwrite` only when translations changed (a plain `-u` does NOT overwrite existing translations).
- Long AI/data scripts: run **server-side via a cron worker** (immune to shell watchdogs), not a long
  `odoo-bin shell` (those get killed after a few minutes). Trigger by setting the cron `nextcall` to the
  past; restore the schedule after.

---

## 3. Architecture & key models (`models/`)

- **`cs.account`** — the central record. Assignment (partner, `csm_user_id`, tier, cadence); 7-factor
  **health score** (support 18% · collections 12% · recency 13% · satisfaction 17% · renewal 12% ·
  usage 13% · AI sentiment 15%, with 90-day exponential decay); churn fields; MRR/renewal; live-computed
  smart-button counts (`_compute_counts`); heavy aggregates via `_recompute_account_metrics()`.
  Builders reused everywhere: **`_build_situation_summary()`**, **`_build_snapshot_trend()`**,
  `_partner_ids()`. Module-level helpers: **`_cs_extract_json(raw)`**, **`_cs_md_to_html(text)`**.
  `write()` auto-runs `_recompute_account_metrics()` (guarded by `cs_skip_recompute` to avoid recursion).
- **`cs.stage`** — lifecycle stages (30-60-90 onboarding → at-risk/churned flags).
- **`csm.offering`** — a service presented to a customer → accepted creates an upsell `crm.lead`.
- **`cs.service`** — Service Catalog (manager-managed; engineers read-only). `action_enrich_from_url`.
- **`cs.weekly.suggestion`** — structured weekly worklist row (priority/reason/guidance/state). Record
  rule scopes it: engineer=own, manager=all.
- **`cs.next.action`** — AI next-best-action history.
- **`csm.kpi.snapshot`** — monthly KPI snapshot (manager trending; **read back into AI** via snapshot_trend).
- **`ir.attachment`** — override `_to_http_stream` to survive missing filestore files (see §7).
- Inherits: `helpdesk.ticket` (sentiment fields + escalation), `res.partner` (cs_account_id), `crm.lead`
  (cs_is_upsell), `voip.call` / `survey.user_input` (`cs_timeline_synced`).
- Wizards (`wizard/`): `cs.customer.import` (Smart Import), `cs.followup.compose`, `cs.call.briefing`,
  `cs.account.copilot`, `cs.capture.request`.

### Security (`security/`)
- 2 groups: `group_era_cs_user` (engineer) and `group_era_cs_manager` (implies user).
- Record rules pattern (in `cs_security.xml`): a `*_own` rule for the user group
  (`[('csm_user_id','=',user.id)]`) + a `*_manager` rule for the manager group (`[(1,'=',1)]`) — OR'd, so
  managers see all. Use this for any new per-CSM model.

---

## 4. The AI layer (EE `ai` module)

12 agents in **`data/ai_agent_data.xml`** (`<data noupdate="1">`):

| Agent | Purpose |
|---|---|
| cs_sentiment_agent | ticket sentiment (health factor) |
| cs_next_action_agent | next best action |
| cs_profile_summary_agent | profile summary under the name |
| cs_service_extract_agent | read a service web page → features/pitch |
| cs_offering_pitch_agent | tailored offering pitch |
| cs_customer_classify_agent | tier classification (Smart Import) |
| cs_churn_forecast_agent | churn p30/p60/p90 + drivers (JSON) |
| cs_renewal_strategy_agent | renewal lever + dated steps (JSON) |
| cs_followup_draft_agent | draft outreach/replies |
| cs_call_prep_agent | pre-call briefing |
| cs_account_qa_agent | Account Copilot Q&A |
| cs_worklist_agent | weekly per-CSM worklist (JSON) |

**Call pattern:**
```python
agent = self.env.ref('era_customer_success.cs_<x>_agent')
resp = agent.with_user(self.env.ref('base.user_root')).get_direct_response(prompt=text)
text = resp[0] if resp else ''           # response is a LIST; [0] is the text
data = _cs_extract_json(text)            # for JSON agents; ALWAYS guard isinstance(data, dict)
```
- `response_style` ∈ `analytical | balanced | creative` only.
- **All AI is opt-in** per company flag (off by default; data leaves to the AI provider):
  `cs_ai_sentiment_enabled`, `cs_ai_next_action_enabled`, `cs_ai_summary_enabled`,
  `cs_ai_churn_enabled`, `cs_ai_renewal_enabled`, `cs_ai_digest_enabled` (Settings → Customer Success).
- **Resilience rules baked in:** wrap each per-account body in `try/except` + log handled transient
  failures as `warning`; guard `isinstance(data, dict)`; for batch crons, commit every ~5 records.
- The AI provider is ERA's CLI transport (`era_ai_accounts`); it occasionally times out — handled, retried.

> ⚠️ **`ai.agent` data is `noupdate=1`** — changing an existing agent's `system_prompt` in XML does NOT
> reach the live record on `-u`. You MUST also write it on prod:
> `env.ref('...cs_worklist_agent').system_prompt = """..."""`. (Hit hard: a JSON-prompt change stayed
> free-text in prod → parser got non-dict → 0 rows.)

---

## 5. Crons (`data/ir_cron_data.xml`, `noupdate=1`) — staggered minutes

12 crons, each on a **distinct minute** to avoid simultaneous runs:

| Cron | When | Method |
|---|---|---|
| recompute_metrics | daily :02 | `_cron_recompute…` |
| advance_lifecycle | daily :07 | lifecycle + at-risk flag |
| renewal_escalation | daily :12 | renewal reminders / overdue |
| cadence | daily :17 | cadence follow-ups |
| build_snapshot | daily :22 | monthly KPI snapshot |
| sync_touchpoints | hourly :27 | feed timeline (idempotent) |
| analyze_sentiment | 2h :32 | `_cron_analyze_sentiment` |
| profile_summary | 3h :37 | `_cron_generate_summaries` |
| next_action | daily :42 | `_cron_suggest_next_steps` |
| forecast_churn | daily :47 | `_cron_forecast_churn` |
| renewal_strategy | daily :52 | `_cron_renewal_strategy` |
| weekly_digest | **Sat 07:00** | `_cron_weekly_digest` → suggestions |

`ir.cron` is `noupdate` too → change schedules on the live DB, then keep the XML seed in sync
(seeds pin `minute=N` so a fresh install can't collide either).

---

## 6. i18n workflow

`i18n/ar.po` (~547 terms; prod locale `ar_AE`). CLI `--i18n-export` was removed in v19, so:
1. Export POT via the `base.language.export` wizard (`lang='__new__'`, format `po`) in a shell.
2. Translate with the generator `/tmp/gen_arpo.py` (a `TRANS` dict + `polib`).
3. Deploy with `-u … --i18n-overwrite`.
Keep new English source strings in code; for view text already written in Arabic, add an identity entry.

---

## 7. Known infra issue (NOT a code bug)

**Prod filestore is ~99% missing** (12,830 `ir.attachment` rows, only ~97 files on disk) — the DB was
restored without its matching filestore, so every image/doc/logo request used to 500. **Mitigated** by
`ir.attachment._to_http_stream` returning an empty stream → placeholder (noise muted; files NOT recovered).
Proper fix = restore the filestore from a backup matching the DB. User chose to leave it muted.

---

## 8. Odoo 19 gotchas (the ones that bit us)

- `res.groups`: `category_id`→`privilege_id` (`res.groups.privilege`); `.users`→`.user_ids`.
- `res.users.groups_id`→`group_ids`; `ir.ui.menu.groups_id`→`group_ids`.
- **Omitting a field from an XML record does NOT reset it on `-u`** — the old value lingers (hit on a
  menu's `group_ids` M2M AND an action's `domain` Char). Set it EXPLICITLY (e.g. `<field name="domain">[]</field>`)
  and/or write it on the live DB.
- `message_post(body=...)` **escapes a plain `str`** → wrap HTML in `markupsafe.Markup`. `mail.message.body`
  returns a `Markup`, so `str.replace()` re-escapes — cast `str(m.body)` first.
- `mail.message.subtype.description` renders as a header line above every message → keep it empty.
- Search view Group-By must be a **bare `<group>`** (no `string`/`expand`).
- `act_window target`: only `current/new/fullscreen/main` (no `inline`).
- `res.partner.mobile` removed; `ir.config_parameter.get_param` returns `False` (not None) when missing.
- Empty recordset is falsy: branch on existence with `'model' in self.env`, guard with `is not None`.
- Full list: see Claude memory `odoo-19-migration-gotchas` (mirrored here).

---

## 9. Roadmap remaining (optional)

From the AI roadmap (most built). Still open:
- **#6 QBR / Business Review generator** — one-click bilingual quarterly review doc from situation +
  snapshot deltas (agent + QWeb PDF). M effort, data ready.
- **#10 Voice-of-Customer theme miner** — monthly cross-account themes from survey free-text + ticket
  descriptions + WhatsApp bodies → `cs.voc.theme` + pivot. M effort, data ready.
- Deferred (need accumulated data / new infra): trained churn ML, embeddings/RAG copilot, VoIP transcript
  miner (#11, needs `era_voip_ai` installed), portfolio NL-query (needs security review).

---

## 10. How to continue elsewhere

1. Clone the module folder; it is self-contained (depends list in `__manifest__.py`).
2. Stand up Odoo 19 EE with the listed deps; install into a throwaway DB; (optionally seed via a script
   like the old `/tmp/seed_cs.py` — 9 demo customers across stages).
3. Follow the validate→deploy loop in §2. Add new AI agents by copying an existing `<record model="ai.agent">`
   and the call pattern in §4. Add new per-CSM models with the record-rule pattern in §3.
4. Remember the `noupdate` traps (§4, §5, §8): XML changes to agents/crons need a live-DB write too.
