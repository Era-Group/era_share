# Changelog — era_customer_success

Odoo 19 Enterprise. All versions deployed to prod DB `ae3229b2-5291-4967-80e1-6368dfecfaae`
(upgrade via `odoo-bin -u era_customer_success -d <prod> --stop-after-init`; workers auto-reload).
Commits pushed to `origin/stage`.

## 19.0.23.0.0 — Guided Operations
- Fixed draft Value Review creation for CSM users: customer and period fields are now editable before preparation, while calculated snapshot values remain protected and the full scope freezes after preparation.
- Reduced chatter noise from automatic health recalculation: score, status, and churn-risk tracking is posted only when the health score moves by 20 points or more in one update.
- Clarified value-review periods with field-level examples and calendar-quarter defaults. The period now identifies the evidence window being reviewed, while the review date identifies the meeting date.
- Added an always-visible self-service guide at the top of every Customer Success screen, wizard, inherited operational form, and executive dashboard. Each guide explains the best use, main features, priorities, and unsafe shortcuts for that specific workflow.

## 19.0.22.0.0 — Self-Explaining Customer Success
- Added complete hover guidance to every module button, covering purpose, prerequisites, side effects, and recommended use without changing workflow or permissions.
- Added actionable empty-state guidance to every window action so users know how records are created, what evidence is required, and the correct next step.
- Added a scoped self-service field-help layer for all Customer Success models and wizards. Existing specialist help is preserved; undocumented fields receive type-aware guidance that distinguishes inputs, workflow choices, computed evidence, AI drafts, dates, and qualification controls.
- Expanded high-risk workflow guidance for service recommendations, opportunity qualification, Voice of Customer, value reviews, adoption evidence, support wallets, success plans, daily work, AI assistance, and customer communication.
- AI guidance consistently states that output is advisory and must be reviewed; service recommendations consistently state that they do not create opportunities before customer need, interest, and timing are confirmed.

## 19.0.21.0.0 — Voice of Customer
- Added immutable Voice of Customer insights captured automatically from closed Value Reviews and confirmed Adoption Assessments.
- Insights preserve source evidence, priorities, risks, response commitments and adoption confidence without copying ticket text or creating chatter events.
- Reliable low adoption and review risks become high-priority customer voice; low-confidence adoption remains medium priority.
- Only high-priority new/triaged insights enter `My Work Today`, below churn/support recovery and above support-hour and routine follow-up work.
- Completing or dismissing the linked work item updates the insight workflow; closing an insight requires a documented response and resolution.
- Added idempotent historical backfill, source-key deduplication, company/CSM record rules, immutable snapshots and workflow-managed audit fields.
- Added a partial unique index plus row locking to guarantee one open work item per customer/week under concurrent cron and user actions.

## 19.0.20.0.0 — Explainable Service Recommendations
- Enriched the ERA catalog with service type, observable need signals, discovery questions, expected customer outcomes, exclusions, recommendation triggers, ticket tags and a re-offer cooldown.
- Added an on-demand recommendation wizard that scores up to three services from confirmed adoption, support pressure, failed SLA, recent ticket tags and explicit success-plan links.
- Recommendations exclude products already purchased, open offerings, and recently rejected services. All reasons are shown before creating records.
- Selected recommendations create idempotent `csm.offering` drafts only; no CRM opportunity is created.
- Added qualification gates: documented customer need, valid customer contact, suitability check, Presented state, confirmed interest and clear timing are required before opportunity handoff.
- Ticket and sales aggregates are restricted to the account company; no ticket content is copied to recommendations.

## 19.0.19.0.0 — Measured Customer Adoption
- Added historical adoption assessments with explicit provenance for active users, licensed users, key-workflow adoption, onboarding completion and usage frequency.
- Adoption score averages only available measured components; data confidence exposes completeness separately so missing telemetry is never presented as low usage.
- Confirmed evidence is immutable, future-dated assessments are rejected, and workflow actions verify record rules before privileged writes.
- Added one prioritized adoption-enablement signal to `My Work Today`; low-confidence signals request data validation instead of urgent intervention.
- Added an opt-in AI enablement-plan drafter. Only aggregate metrics and the optional blocker description are sent; evidence text and customer records are excluded.
- Value Reviews now freeze and send the measured adoption score, confidence, status and evidence date as part of the QBR snapshot.
- Completed Arabic translations for adoption screens, statuses, validation messages and AI controls; removed duplicate PO entries.

## 19.0.18.0.0 — Customer Value Reviews (QBR)
- Added periodic value reviews that freeze health, satisfaction, support hours, objectives, criteria and milestone progress at preparation time.
- Added a value-led workflow: Draft → Prepared → Held → Closed, requiring customer-confirmed value, commitments and the next review date before closure.
- Closing a review updates the active success-plan review date and schedules one native Odoo follow-up activity when a next step is agreed. It never creates an offering or CRM opportunity.
- Added an idempotent daily cron that creates upcoming reviews from active success plans 14 days before their review date and routes them into `My Work Today`.
- Added an independent opt-in AI drafter for grounded agendas, observations, questions, risks and needs to validate. AI cannot populate confirmed value or commitments.
- Added persistent `doc/PROJECT_MEMORY_AR.md` and made updating it part of the release checklist.

## 19.0.17.0.0 — Automated Support Hours
- Added a read-only Customer Success wallet over Odoo's native prepaid Sales/Timesheet balance; no hours are duplicated or manually maintained.
- Support packages already linked to Helpdesk are detected automatically, while managers can configure additional prepaid time products and company-level validity/alert thresholds.
- Added live purchased, used, remaining, expiry and balance-status visibility per customer. Exhausted, expired, critical, low and expiring packages feed one prioritized item into `My Work Today`.
- Added a one-click `Explore Need` path that starts a draft support-hours offering with the current usage evidence. It creates no CRM opportunity until the customer confirms interest and the CSM qualifies it.

## 19.0.16.0.0 — Customer Success Plans
- Added one easy-to-open success plan per customer with business objectives, current challenges, desired outcomes, measurable success criteria and ERA's next value plan.
- Added a stakeholder map linked only to known customer contacts, including role, influence and relationship strength.
- Added owned, dated success milestones with evidence, blockers and lifecycle. Overdue or near-term milestones automatically enter `My Work Today` as the account's highest relevant intervention.
- Added an opt-in Odoo AI agent that drafts only missing plan content, links only known contacts, proposes 3-5 reviewable milestones, and never overwrites or duplicates existing work.

## 19.0.15.0.1 — Keep Today's worklist focused
- Open work items from previous weeks are now dismissed automatically as superseded when the daily list is built. Their history and outcome remain available, while `My Work Today` stays limited to actionable current work.

## 19.0.15.0.0 — Daily Customer Success work center
- Added a deterministic daily worklist that prioritizes risk recovery, support recovery, renewal attention, overdue relationship cadence, and low adoption. It works without AI; the existing weekly AI worklist enriches the same durable records when enabled.
- Weekly generation no longer deletes completed work history. One work item per customer/week is updated only while open.
- Completing or dismissing an item now requires a concise outcome. A next step can be recorded and scheduled automatically as a native Odoo activity.
- Added `My Work Today` and manager `Team Worklist` views with Today, Overdue, priority, work-type, owner, outcome, and next-step visibility.

## Current: 19.0.14.6.1

### 19.0.14.6.1 — Fix: WhatsApp send from the AI composer crashed
- **14.6.1** — Removed an orphaned `self.action_log_note()` call in `cs.followup.compose.action_send` (WhatsApp branch). The method was deleted in 14.5.x ("zero AI output in chatter") but the call was left behind, so **Send** on the WhatsApp channel raised `AttributeError: 'cs.followup.compose' object has no attribute 'action_log_note'`. The draft now routes straight to the native WhatsApp composer.

### 19.0.14.6.0 — Remove the `sadeem_waha_whatsapp` (WAHA) integration
- **14.6.0** — Dropped the WAHA integration entirely; the module now relies only on **native** Odoo WhatsApp (`whatsapp`). Removed the `sadeem_waha_whatsapp` dependency, the `models/waha_message.py` model (`sadeem.waha.whatsapp.message` inherit + `cs_timeline_synced` field), the `cs_whatsapp_provider` company/settings selector and its "Messaging" settings block, and the `action_cs_whatsapp_wizard` method. Rewired: the WhatsApp smart button (`whatsapp_count` / `action_view_whatsapp`) now counts and opens **native WhatsApp discuss channels** (via `res.partner.action_open_partner_wa_channels`); `action_cs_send_whatsapp` always opens the native `whatsapp.composer`; `_recompute_account_metrics` no longer folds WAHA messages into `last_touch_date`; `_cron_sync_touchpoints` no longer mirrors WAHA messages (removed `_sync_waha_messages`); the AI Follow-up Composer routes WhatsApp through the native composer (no more WAHA media send wizard).

### 19.0.14.5.14 — Hide the WAHA WhatsApp icon on the account form
- **14.5.14** — Removed the yellow WAHA WhatsApp icon button (`action_cs_whatsapp_wizard`) next to the phone field on the `cs.account` form, per request. The method is kept on the model (unused by the view now) in case it's needed later.

### 19.0.14.5.13 — "Send Email" button mirroring the WhatsApp template
- **14.5.13** — Added an **"إرسال إيميل"** button on the offering (next to the WhatsApp one) that emails the customer the SAME content as the service WhatsApp template. `cs.service._build_email_body()` renders the WhatsApp body (`*bold*`→`<b>`, newlines→`<br/>`) as HTML with the service image header (base64 data-URI → Odoo's `mail.message.create()` inlines it as an attachment on send), the "اعرف المزيد" URL button, and the "مجموعة إيرا" footer. `csm.offering.action_send_service_email()` opens `mail.compose.message` in `comment` mode on the linked `cs.account` (logs on the account chatter + emails the customer via `default_partner_ids`).

### 19.0.14.5.12 — Bind service WhatsApp templates + send to cs.account (not res.partner)
- **14.5.12** — Reverted the `res.partner` binding/routing from 14.5.11. Service templates now bind to the **Customer Success model `cs.account`** (`phone_field='phone'`), and `csm.offering.action_send_service_whatsapp()` opens the offering's **linked `cs.account` record** (`cs_account_id`, same customer) — the model the template belongs to — instead of `res.partner`. Catalog template-creation now targets `cs.account`. The 18 existing templates were already on `cs.account`.

### 19.0.14.5.11 — Use service WhatsApp templates across models (offering → customer)
- **14.5.11** — WhatsApp templates are bound to a single model in Odoo, so a `res.partner`-bound service template doesn't appear from other models (e.g. `csm.offering`). Rather than duplicate templates per model (= N Meta approvals), the architecture keeps ONE template per service bound to **res.partner** (the recipient) and routes sends through it: `csm.offering.action_send_service_whatsapp()` (+ form button "إرسال قالب الخدمة (واتساب)") opens the **customer's** WhatsApp composer with the offering's `service_id.whatsapp_template_id` preselected. The same pattern extends to any model with a partner + service. (Sending requires the template to be Meta-approved.)

### 19.0.14.5.10 — WhatsApp template per service (Meta-approved)
- **14.5.10** — `cs.service` gains an `action_create_whatsapp_template()` button ("إنشاء/فتح قالب واتساب", manager-only) that creates (or opens) a Meta `whatsapp.template` for the service: marketing category, Arabic, **image header** (the service image, else a text header with the service name), body built from name + description + key features (plain text, no variables → easiest to approve), footer "مجموعة إيرا", and a "اعرف المزيد" URL button when the service has a URL. Targets `res.partner`/`phone`. Templates are created as **drafts** for review — submit to Meta from the template form (`button_submit_template`). New `whatsapp_template_id` link on the service. Generated drafts for the 18 existing active services.

### 19.0.14.5.9 — Fix AccessError when generating AI follow-up / briefing / copilot
- **14.5.9** — `_build_situation_summary` read accounting-gated fields (e.g. `followup_status`) as the current user, so a CS user without accounting rights hit `AccessError: ... field "followup_status"` when generating an AI follow-up draft (also affects call briefing & copilot — all build the prompt as the user before `with_user(root)`). The summary is a read-only aggregation for the AI prompt, so it now reads with `self.sudo()`. Found via log: "CS follow-up draft failed for account 73".

### 19.0.14.5.8 — Averaged scores ignore no-data accounts
- **14.5.8** — Full per-measure aggregation review of the CS KPIs pivot. Confirmed: counts (activities, calls, activities_done, open_tickets, meetings) and money (MRR, upsell_revenue, overdue_amount) = **SUM**; scores/ratios (health_score, CSAT, NPS, sentiment, avg_resolution_hours, days_since_touch) = **AVG**. Fixed the averages to be meaningful: the snapshot now stores **NULL** (not 0) for `csat`/`nps`/`avg_resolution_hours` when there's no data, and for `sentiment` when the account has no AI sentiment label — so an unrated/unanalysed account no longer drags the team average toward 0 (e.g. CSAT was showing ~1.4 because most accounts are unrated). SQL AVG ignores NULL.

### 19.0.14.5.7 — Score measures average (not sum) in pivots
- **14.5.7** — Score/ratio measures were summing across an engineer's accounts in pivots (e.g. Health Score per engineer showed ~320 = 4 accounts × ~80, exceeding 100). Set `aggregator='avg'` on all score/ratio fields so they average correctly: `cs.account` (health_score, churn_probability, churn_prob_30/60, csat_latest, nps_latest, sentiment_score) and `csm.kpi.snapshot` (health_score, csat, nps, sentiment, avg_resolution_hours, days_since_touch). Additive measures (MRR, tickets, meetings, calls, activities, revenue) keep SUM.

### 19.0.14.5.6 — Remove standalone Engineer Activity report
- **14.5.6** — Removed the standalone **Engineer Activity** report (`cs.engineer.activity` model, views, menu, security, SQL view) added in 14.5.4 — now redundant since the **الأنشطة** (Activities) measure lives on the main CS KPIs & Reporting pivot (14.5.5).

### 19.0.14.5.5 — "Activities" measure on the CS KPIs & Reporting pivot
- **14.5.5** — Added period-bounded, **actor-based** engineer-activity measures to `csm.kpi.snapshot`: `calls_made` (VoIP calls the account's engineer placed to the account), `activities_done` (activities the engineer completed on the account), and **`activities`** = their sum (Arabic label **الأنشطة**, set on `ir.model.fields`). Surfaced as the lead measures on the KPIs pivot so the headline number is *engineer activity*, not the built-in **Count** (التعداد) which counts snapshot rows = accounts per engineer. Attribution is the account's CSM as the doer (counting calls *to* the account by anyone would miscredit the owner — the data showed engineers placing calls on each other's accounts). Period bound uses `create_date` (call `start_date` is often NULL). Existing snapshots back-filled via `_cron_build_snapshot`. NOTE: this counts each engineer's work on their **own** accounts this period; the standalone **Engineer Activity** report (v14.5.4) shows the full actor view across all CS accounts, all-time.

### 19.0.14.5.4 — New "Engineer Activity" report (realistic work performed)
- **14.5.4** — Added `cs.engineer.activity` (read-only SQL view, manager-only, under CS KPIs & Reporting) measuring what each engineer actually **did**, attributed to the performer: (a) activities they **completed** (done-activity messages they authored on accounts — pending AI/system to-dos are excluded) + (b) **VoIP calls** they placed to a CS-account customer. Pivot/graph/list; default measure Count (التعداد) = real work per engineer. This replaces the account-count confusion in the KPI snapshot pivot (whose Count = accounts/snapshots per engineer, not activities).

### 19.0.14.5.3 — Auto-activities created by OdooBot + remove redundant Engineer Activity report
- **14.5.3** — *Realistic employee-activity reporting.* Module-automated activities (kickoff, at-risk intervention, renewal reminder, follow-up cadence, survey/service recovery, renewal play, sentiment recovery) are now created by **OdooBot** (`created_by = OdooBot`) via a new `cs.account._auto_activity_schedule()` helper (`with_user(SUPERUSER_ID)`). This makes AI/system activities clearly distinguishable from activities an employee schedules personally — so activity/workload reports can show *real* employee activity by excluding OdooBot-created rows. Employee-initiated actions (Log Call) keep the employee as creator. Existing kickoff activities back-filled to OdooBot.
- **14.5.3** — Removed the **Engineer Activity** report (`csm.engagement` model, views, menu, security, SQL view) as redundant.

### 19.0.14.5.2 — Fix OwlError opening the Engineer Activity report
- **14.5.2** — The "Engineer Activity" report crashed on open (OwlError → `Function.prototype.apply was called on undefined`): the default "This Month" search filter used `context_today().replace(day=1)`, and `.replace()` is not supported by Odoo's client-side py evaluator. Switched to the idiomatic `context_today() + relativedelta(day=1)`.


### 19.0.14.5.1 — Weekly worklist runs detached (no cron blocking)
- **14.5.1** — Reverted the 14.5.0 daily catch-up-cron approach. Instead, `_cron_weekly_digest` now launches the slow per-CSM AI generation in a **detached background thread** (own cursor, commits per CSM) and returns immediately, so the cron worker is never blocked. A Postgres session **advisory lock** prevents two runs overlapping; progress survives an interruption (per-CSM commit). No extra cron.

### 19.0.14.4.1 — AI crons no longer freeze the system
- **14.4.1** — The sentiment / next-action / profile-summary crons processed their whole batch (50 tickets / 20 / 15 accounts) of slow AI calls in a **single transaction**, holding row locks for the full run (the sentiment cron actually hit the 1200s worker time-limit and was killed) — so any UI edit or other cron touching those rows blocked and the system looked frozen during cron runs. They now use `_run_ai_cron_per_account` (**one record per transaction, commit after each**, like churn/renewal): locks release within seconds, progress is saved on timeout, and a conflict is isolated to one record. Sentiment batch reduced 50→20 per run.


### 19.0.14.4.0 — Sentiment hover breakdown + no duplicate scheduled activities
- **14.4.0 — Sentiment analysis details on hover.** New OWL field widget `cs_sentiment_badge` on the account's `sentiment_label`: hovering the badge shows a tooltip with the per-ticket breakdown behind the score (date · label · score · reason, newest first, recent weighted higher) sourced from a new read-only computed field `sentiment_detail`. Files: `static/src/js/cs_sentiment_field.js`, `static/src/xml/cs_sentiment_field.xml`, tooltip CSS in `cs_dashboard.css`.
- **14.4.0 — No duplicate "Renewal play" activities.** `action_renewal_strategy` now caps at one open *Renewal play* activity per account (same marker-prefix guard as the sentiment-recovery escalation). `_cron_renewal_strategy` re-runs daily over every renewal-soon account, so without the guard an at-risk account accumulated a fresh activity every single day. Audited all `activity_schedule` sites: kickoff (create-only), at-risk (stage-transition guarded), renewal reminders (exact-date), cadence (`has_open` guarded), survey recovery (sync-flag guarded), sentiment recovery (marker guarded) — all idempotent. Verified prod: zero CS-activity duplicates (the only DB duplicates are native `hr.appraisal` forms).

### 19.0.14.3.0 — Durable sentiment cron, 1-year window, zero AI in the chatter
- **14.3.0 — Sentiment cron is now durable.** `_cs_analyze_sentiment` commits **after each ticket**, so a single slow/hanging AI call can no longer roll back the whole batch on a cron timeout (the previous behaviour left every ticket unanalysed — sentiment stuck at 0). Skips commits under a `TestCursor` to preserve test isolation. Backfilled all 39 analysed tickets on prod.
- **14.3.0 — Account sentiment window widened 90 → 365 days** (`_CS_SENTIMENT_WINDOW_DAYS`, `_CS_SENTIMENT_HALFLIFE_DAYS = 90`). Customers open tickets infrequently, so a 90-day window left quiet accounts permanently neutral/0; the most recent analysed ticket now contributes (exponential decay, recent dominates). `شركة الإطار الأول` → **-22 (negative)**.
- **14.3.0 — No AI output is ever posted to the chatter** (per request — the timeline is for real customer touch-points and manual activities only; all AI results live in form fields):
  - Removed the automated **Next Best Action** chatter post (kept the form field + `cs.next.action` history log).
  - Removed the **AI service-enrichment** notice post (`cs.service` — details/timestamp shown in form).
  - Removed the AI **sentiment mood emoji** from the "Ticket closed" timeline line (factual touch-point only).
  - Removed the three **"Log to Timeline"** buttons + actions from the AI wizards (Pre-Call Briefing, Follow-up Composer, Account Copilot) — their results stay in the wizard form. Cleaned now-unused `Markup`/`html_escape`/`_cs_md_to_html` imports.

### 19.0.14.2.3 — WhatsApp icon opens the WAHA send wizard
- **14.2.3** — The yellow WhatsApp icon now opens the WAHA **"Send WhatsApp Message"** wizard (`action_cs_whatsapp_wizard` → `res.partner.action_send_whatsapp`) instead of the read-only chat viewer, so you can compose/send directly.

### 19.0.14.2.2 — Yellow WhatsApp icon in the contact row
- **14.2.2** — Added a yellow WhatsApp icon (`fa-whatsapp`, `#F1C40F`) next to the phone in the `cs.account` form's contact row → `action_cs_send_whatsapp`, which opens the customer's WAHA conversation.

### 19.0.14.2.1 — Remove the header WhatsApp button
- **14.2.1** — Removed the yellow WhatsApp button from the `cs.account` form header (per request). WhatsApp is still reachable via the WhatsApp smart button and the AI Follow-up Composer.

### 19.0.14.2.0 — Assignment access, fair import, sentiment off-chatter
- **14.2.0** — Three improvements:
  - **Auto-grant access on assignment**: setting an account's `csm_user_id` now adds that engineer to the **Customer Success Engineer** group automatically (`_ensure_csm_in_group`, additive only). Fixes "assigned the customer but the engineer sees nothing" — assignment alone never granted the group before.
  - **Fair import distribution**: Smart Customer Import now assigns customers to the **least-loaded** engineer (seeded from current portfolios) instead of `random.choice`, so no engineer is left with zero.
  - **Sentiment off the chatter**: `_cs_sentiment_escalate` no longer posts a "negative sentiment" note to the account timeline for every ticket on every cron run (that flooded the chatter). The latest sentiment is shown on the form (badge + score); the capped service-recovery activity is unchanged.

### 19.0.14.1.2 — Stop cron flooding (activities + chatter)
- **14.1.2** — Two root-cause fixes for unprofessional cron flooding:
  - **Recovery-activity flood**: `_cs_sentiment_escalate` created a *separate* "Service recovery" activity for **every** negative-sentiment ticket, piling dozens of overdue tasks on the CSM. Now capped to **one open recovery activity per account** (matched by the translated summary prefix, language-independent). Cleaned up existing data: kept 1 per account, removed 68 duplicates.
  - **Chatter ticket-history dump**: the touch-point sync (`_sync_helpdesk_tickets` + calls/WhatsApp/surveys) mirrored a customer's **entire** closed-ticket/message history into the timeline on first sync (e.g. right after creating an account). Now only events newer than `_CS_SYNC_RECENCY_DAYS` (2 days) are posted; older history is marked synced **silently**. The sync runs daily, so genuinely new touch-points are still captured.

### 19.0.14.1.1 — WhatsApp send button on the account form
- **14.1.1** — Added a prominent **yellow WhatsApp button** (`fa-whatsapp`, `btn-warning`) to the `cs.account` form header → `action_cs_send_whatsapp` (opens the WAHA conversation / native composer per the company's WhatsApp provider). Previously only a view-only WhatsApp smart button existed.

### 19.0.14.1.0 — Engineer Activity report (weekly messages & activities per CSM)
- **14.1.0** — New **"Engineer Activity"** report (Reporting menu, manager-only): read-only SQL-view model `csm.engagement` with one row per engagement event — logged **call / WhatsApp / SMS / email**, **done** activity, **scheduled** activity, or **meeting** — attributed to the engineer who owns the customer's account (`cs.account.csm_user_id`). Pivot (rows = engineer × type, columns = week), stacked graph, and list drill-down; search filters This Week / This Month / by category; meetings deduped per event+account. Communications matched to the account via the customer's commercial partner; activities read off the account timeline.

### 19.0.14.x — Service catalog image + use in messaging
- **14.0.3** — Smart Customer Import now **excludes archived (inactive) companies** from candidates: `_candidate_partners()` filters `p.is_company and p.active` (was `is_company` only), so an archived customer is never re-imported. Affects both the candidate count and the actual import.
- **14.0.2** — Restrict the Service Catalog **"Read from URL (AI)"** button (`action_enrich_from_url`) to the Customer Success **Manager** group only (`groups="era_customer_success.group_era_cs_manager"`). Engineers already have the catalog read-only, so the AI-enrich action no longer shows for them.
- **14.0.1** — **Churn/renewal AI cron serialization hardening**: `_cron_forecast_churn` failed with `psycopg2.errors.SerializationFailure: could not serialize access due to concurrent update` at its batch `cr.commit()` — it batched 5 slow AI calls into one ~2-min transaction, so the final flush of `cs_account` churn fields raced with concurrent writes to the same rows (other CS crons, the write-triggered metric recompute, user edits); Odoo crons aren't auto-retried so the whole run died. New `_run_ai_cron_per_account()` runs one account per transaction (commit after each) and catches `SerializationFailure` → rollback + skip that account (retried next run). Applied to both `_cron_forecast_churn` and the identical `_cron_renewal_strategy`.
- **14.0.0** — **Service image (#)**: `cs.service` now inherits `image.mixin` (image_1920 + resized variants). Image shown on the catalog form (avatar), kanban card thumbnail and list. Surfaced read-only on the offering form (`service_image`). **Usable in messaging**: the AI Follow-up Composer (`cs.followup.compose`) gains an optional **Attach Service** picker — the service image is sent with the message and its details steer the AI draft. Per channel: **email** attaches the image natively (`default_attachment_ids`); **WhatsApp** (WAHA) opens the WAHA send wizard prefilled with the image as a media message (`file_upload`) + the draft as caption; **SMS** sends text only (no media). The image is also attached to the timeline log note so a copy is always saved. New `cs.service` helpers: `_cs_message_attachment()`, `_cs_image_filename()`, `_cs_image_mimetype()`.

### 19.0.13.x — Weekly Suggestions (structured worklist) + menu/permission polish
- **13.4.0** — Set Weekly Suggestions action `domain` to `[]` explicitly (an absent XML field is NOT cleared on `-u`; the old `uid` filter lingered and forced "own" even for managers).
- **13.3.0** — Main "Weekly Suggestions" page adapts by role via record rule: engineer sees own, manager sees ALL CSMs' suggestions for follow-up (removed the hard `uid` domain).
- **13.2.0** — Moved "Smart Customer Import" menu into Configuration submenu.
- **13.1.0** — Service Catalog visible to engineers again (read-only: kanban/list/form, no New/Edit); removed redundant "All Accounts" menu.
- **13.0.0** — **Weekly Suggestions**: new structured model `cs.weekly.suggestion` (one row per account needing attention: priority, reason, guidance/التوجيه, health+churn snapshot, To-Do/Done status). Worklist agent now returns JSON. Engineer page (own) + manager page (all, grouped by CSM) + "Generate Now" menu. Replaced the free-text inbox digest.

### 19.0.12.x — Weekly digest + UX
- **12.1.0** — Restrict Service Catalog management to managers (engineer read-only).
- **12.0.0** — **Weekly CSM worklist digest (#14)** (originally inbox delivery); **auto-recompute metrics on every write** (cs_skip_recompute guard, hid the Recompute button); moved Meeting/Schedule-Follow-up header buttons to the chatter's native activity scheduler.
- Cron schedule: weekly digest fires **Saturday 07:00**; all **12 crons staggered onto distinct minutes** (2,7,12,17,22,27,32,37,42,47,52 + digest :00).

### 19.0.11.0.0 — Account Copilot + infra resilience
- **Account Copilot (#9)**: `cs.account.copilot` wizard + `cs_account_qa_agent` — ask any NL question about a customer, grounded in its data/timeline. "Ask AI" header button.
- **Filestore resilience**: `ir.attachment._to_http_stream` degrades gracefully on missing files (empty stream → placeholder) instead of HTTP 500. Mutes the prod filestore-loss noise (~99% of filestore files missing — DB restored without filestore; surfaced to user, left as-is by choice).
- Cleaned 815 historical timeline messages that had escaped `&lt;b&gt;`.

### 19.0.10.x — AI hardening + chatter HTML
- **10.4.0** — handled transient AI per-account failures logged as `warning` not `error+traceback` (less monitor noise).
- **10.3.0 / 10.2.0** — chatter renders AI HTML (wrap bodies in `markupsafe.Markup`; Odoo 19 escapes plain-str bodies); cleared redundant subtype `description` line; hover-help on every header + smart button; safer cron batch size (5).
- **10.1.0** — hardened churn/renewal AI batches against malformed replies (whole per-account body in try/except + `isinstance(data, dict)` guard).
- **10.0.0** — **AI Pre-Call/Meeting Briefing** wizard (`cs.call.briefing` + `cs_call_prep_agent`). NOTE: the VoIP transcript miner (#11) was dropped — `era_voip_ai` is uninstalled in prod (no `crm.realtime_call_summary`).

### 19.0.7–9 — Predictive + generative AI (roadmap increments 1–3)
- **9.0.0** — **AI Follow-up / Reply Composer** (`cs.followup.compose` + `cs_followup_draft_agent`): drafts WhatsApp/SMS/email in the customer's language, routes to the native composer.
- **8.0.0** — **AI Renewal Strategy Agent** (`cs_renewal_strategy_agent`): lever (collections/pitch/re-engage/standard) + dated countdown steps; window-gated daily cron.
- **7.0.0** — **AI Churn Probability Forecaster** (`cs_churn_forecast_agent`): p30/p60/p90 + top drivers, grounded on situation + new `_build_snapshot_trend()` KPI history; Python hard-signal floor; daily cron on all active accounts.
- **6.1.0** — hover-help tooltip on the Recompute button.
- **6.0.0** — AI Profile Summary under the customer name (gradual cron).

### 19.0.3–5 — AI foundation + import
- **5.x** — Smart Customer Import (`cs.customer.import` wizard + `cs_customer_classify_agent`, dedup); tier priority stars; Arabic refinements.
- **4.0.0** — Service Catalog (`cs.service`, `action_enrich_from_url` reads a service page via requests+html2plaintext+AI); offering AI-tailored pitch (`cs_offering_pitch_agent`).
- **3.0.0** — AI Next Best Action (`cs.next.action` + `cs_next_action_agent`).

### 19.0.1–2 — Core platform
- **2.x** — VoIP click-to-dial; OWL Executive Dashboard; sentiment mood icons + trend graph; native phone widget; live-computed smart-button counts; full Arabic `i18n/ar.po`.
- **1.1.0** — AI sentiment analysis (`cs_sentiment_agent`) as a health factor.
- **1.0.0** — core `cs.account` (health score, 30-60-90 lifecycle, unified timeline, smart buttons), `cs.stage`, `csm.offering`, `csm.kpi.snapshot`, manager dashboard, 2 security groups, sync crons.
