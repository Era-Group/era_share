# CRM AI Development — مشروع الوكلاء الذكية لإدارة المبيعات

## Project Description

**Client:** Era Group — Odoo Partner in Saudi Arabia  
**Market:** Saudi Arabia & Gulf Region  
**Platform:** Odoo CRM (modules live in `era_share_latest` only)

This project delivers **15 AI Agents** for sales pipeline management and customer relationship intelligence inside Odoo CRM. The agents cover the full sales lifecycle: lead resurrection, dormant customer detection, enrichment, scoring, daily actions, deal health, WhatsApp intelligence, account briefs, inbound qualification, action-capable bots, bilingual proposals, compose quality, Arabic content, roleplay training, and PDPL compliance.

### Key Principles

- **Multi-LLM Routing:** Use a cheap model (e.g., GPT-4o-mini, Haiku) for simple agents and an advanced model (Claude, GPT-4o) for sensitive/complex tasks — drastically reduces operating cost.
- **Human-in-the-Loop:** All Arabic outbound messages require human review before sending — fully autonomous generation produces weak/repetitive content.
- **PDPL Compliance First:** No message leaves the system without explicit consent, opt-out within 72h, and DSAR support (Agent #15 is a prerequisite for all outbound agents).
- **Cost Caps:** Hard ceiling on AI API costs in code and dashboard (Rule 14).
- **Security:** API keys in `process.env` only (Rule 03). Agents operate within `res.users`/`res.groups` permissions, never superuser (Rule 09). Critical actions logged (Rule 20).
- **Code Location:** All modules built in `era_share_latest` — never modify Odoo CE/EE core or `waha`.

---

## Agents Summary

| # | Agent | Phase | Duration | Depends On |
|---|-------|-------|----------|------------|
| 15 | Compliance Guardrail — حارس الامتثال | Phase 1 | 1–1.5 weeks | — (prerequisite for all) |
| 3 | Waterfall Enrichment Engine — محرك الإثراء | Phase 1 | 1–1.5 weeks | — |
| 1 | Dead-Lead Resurrection — إحياء الفرص الميتة | Phase 2 | 1 week | #15, #3 |
| 2 | Dormant Gold Detector — كاشف الكنوز النائمة | Phase 2 | 1 week | #3 |
| 4 | Explainable Lead Score — التقييم التنبؤي | Phase 2 | 1 week | — |
| 9 | Inbound WhatsApp Qualifier — بوت تأهيل الوارد | Phase 2 | 1 week | #15 |
| 5 | Daily Prioritized Action List — قائمة الإجراءات | Phase 3 | 1–1.5 weeks | #4 |
| 6 | Deal-Risk Watchdog — حارس صحة الصفقات | Phase 3 | 1 week | — |
| 7 | WhatsApp Conversation Intelligence — ذكاء المحادثات | Phase 3 | 2–3 weeks | — |
| 8 | Account Brief Generator — مولّد إحاطة الحساب | Phase 3 | 1 week | — |
| 10 | Action-Capable WhatsApp Agent — وكيل واتساب قادر | Phase 4 | 3 weeks | #9, #15 |
| 11 | Bilingual Proposal Generator — مولّد العروض | Phase 4 | 1–2 weeks | — |
| 12 | Live Compose Quality Score — مؤشر جودة الكتابة | Phase 4 | 1–2 weeks | — |
| 13 | Arabic Content Generator — مولّد المحتوى العربي | Phase 4 | 1–2 weeks | — |
| 14 | AI Roleplay Trainer — تدريب الأدوار | Phase 4 | 3 weeks | — |

---

## Build Phases & Timeline

```
Phase 1 (Weeks 1-2)    ──────────────────────────────
  ├── Agent 15: Compliance Guardrail  ← MUST be first
  └── Agent 3:  Waterfall Enrichment Engine (parallel)

Phase 2 (Weeks 3-4)    ──────────────────────────────
  ├── Agent 1:  Dead-Lead Resurrection      (needs #15, #3)
  ├── Agent 2:  Dormant Gold Detector       (needs #3)
  ├── Agent 4:  Explainable Lead Score      (independent)
  └── Agent 9:  Inbound WhatsApp Qualifier  (needs #15)

Phase 3 (Weeks 5-7)    ──────────────────────────────
  ├── Agent 5:  Daily Action List           (needs #4)
  ├── Agent 6:  Deal-Risk Watchdog          (independent)
  ├── Agent 7:  WhatsApp Conv. Intelligence (independent)
  └── Agent 8:  Account Brief Generator     (independent)

Phase 4 (Weeks 8-12)   ──────────────────────────────
  ├── Agent 10: Action-Capable WhatsApp     (needs #9, #15)
  ├── Agent 11: Bilingual Proposal Gen.     (independent)
  ├── Agent 12: Live Compose Quality Score  (independent)
  ├── Agent 13: Arabic Content Generator    (independent)
  └── Agent 14: AI Roleplay Trainer         (independent)
```

### Dependency Graph

```
#15 Compliance ──┬──► #1 Dead-Lead Resurrection
                 ├──► #9 Inbound WhatsApp Qualifier ──► #10 Action-Capable WhatsApp
                 └──► #10 Action-Capable WhatsApp

#3 Enrichment  ──┬──► #1 Dead-Lead Resurrection
                 └──► #2 Dormant Gold Detector

#4 Lead Score  ──────► #5 Daily Action List

#6, #7, #8, #11, #12, #13, #14 — Independent (can start anytime in their phase)
```

---

## Shared Infrastructure

All agents share these components:

| Component | Description |
|-----------|-------------|
| **LLM Router** | Multi-LLM routing service — dispatches to cheap/advanced model based on task complexity |
| **Enrichment Pipeline** | Waterfall enrichment (Agent #3) — shared by #1, #2, and others |
| **Arabic Content Engine** | Arabic NLP + cultural guardrails — shared by #1, #7, #9, #11, #12, #13 |
| **Compliance Layer** | PDPL consent + opt-out + DSAR (Agent #15) — shared by all outbound agents |
| **waha Integration** | WhatsApp API via waha — shared by #1, #7, #9, #10 |
| **Event Logger** | Critical action audit log (Rule 20) — shared by all agents |

---

## Cost Estimates (Monthly Operating)

| Agent | Cost/unit | Estimated Monthly |
|-------|-----------|-------------------|
| #1 Dead-Lead | $0.01-0.03/message | $5-10 (200 leads) |
| #2 Dormant Gold | $0.05-0.15/customer | Variable |
| #3 Enrichment | $0.005/record | Variable |
| #4 Lead Score | ~$0 (local model) | Minimal |
| #5 Daily Actions | 1 LLM call/rep/day | $5-15 |
| #6 Deal Watchdog | 1 LLM call/deal/day | $10-20 |
| #7 Conv. Intelligence | $0.02-0.06/conversation | Variable |
| #8 Account Brief | On-demand | Minimal |
| #9 Inbound Qualifier | Per conversation | $20-50 |
| #10 Action WhatsApp | Per conversation + API | $30-60 |
| #11 Proposal Gen | Per proposal | Minimal |
| #12 Quality Score | Batched calls | Minimal |
| #13 Arabic Content | Per content piece | Variable |
| #14 Roleplay Trainer | Per session | $20-40 |
| #15 Compliance | Rule-based (no LLM) | ~$0 |

---

## Task Files

Each agent has a detailed development prompt in `tasks/`:

- [Task 01 — Dead-Lead Resurrection](tasks/task_01_dead_lead_resurrection.md)
- [Task 02 — Dormant Gold Detector](tasks/task_02_dormant_gold_detector.md)
- [Task 03 — Waterfall Enrichment Engine](tasks/task_03_waterfall_enrichment.md)
- [Task 04 — Explainable Lead Score](tasks/task_04_explainable_lead_score.md)
- [Task 05 — Daily Action List](tasks/task_05_daily_action_list.md)
- [Task 06 — Deal-Risk Watchdog](tasks/task_06_deal_risk_watchdog.md)
- [Task 07 — WhatsApp Conversation Intelligence](tasks/task_07_whatsapp_conv_intelligence.md)
- [Task 08 — Account Brief Generator](tasks/task_08_account_brief_generator.md)
- [Task 09 — Inbound WhatsApp Qualifier](tasks/task_09_inbound_whatsapp_qualifier.md)
- [Task 10 — Action-Capable WhatsApp Agent](tasks/task_10_action_capable_whatsapp.md)
- [Task 11 — Bilingual Proposal Generator](tasks/task_11_bilingual_proposal_generator.md)
- [Task 12 — Live Compose Quality Score](tasks/task_12_live_compose_quality_score.md)
- [Task 13 — Arabic Content Generator](tasks/task_13_arabic_content_generator.md)
- [Task 14 — AI Roleplay Trainer](tasks/task_14_ai_roleplay_trainer.md)
- [Task 15 — Compliance Guardrail](tasks/task_15_compliance_guardrail.md)
