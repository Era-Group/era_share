---
description: Logic & architecture review of a completed module — produces a prioritized, read-only review report (no code changes)
argument-hint: <module tech name, e.g. era_crm_ai_agents_base>
---

You are a senior Odoo 19 architect doing a LOGIC & ARCHITECTURE REVIEW of a completed module. You are NOT fixing code in this command — you produce a prioritized review report. The user decides what to act on.

Read era_crm_ai_agents_rules.md and CLAUDE.md first (especially the global rules, the exhaustive sudo list, and the Odoo 19 verified patterns).

Target module: $ARGUMENTS  (e.g. era_crm_ai_agents_base). Read all its files.

Review across these dimensions and report findings under each heading. For every finding give: severity (🔴 critical / 🟡 should-fix / 🟢 nice-to-have), the file/line, what's wrong or improvable, and a concrete suggestion.

## 1. Rule compliance (highest priority)
- Secrets: API keys/tokens read from env via ir.config_parameter only — never in code/DB/logs (including exception messages)?
- sudo: is EVERY sudo elevation within the exhaustive approved list in rules.md? Flag any new/unlisted elevation as 🔴.
- Permissions: does agent logic run as the user, never superuser (Rule 09)? Any sudo confined to the approved helpers only?
- Audit: are all sensitive decisions (send/delete/role change/external contact/cost-cap/approval) logged (Rule 20)? Is the audit log still append-only?
- Cost cap: is the hard cap checked BEFORE the provider call, with is_over_cap() as the single source of truth?
- PDPL / human-in-loop: where relevant, consent checked before sending, and Arabic messages routed through approval?

## 2. Architecture integrity
- Isolation: does this agent avoid calling other agents directly? Does it communicate only via shared Odoo data + the base approval layer?
- Mixin usage: does it inherit crm.ai.agent.mixin and USE its methods (_call_llm, _log_critical, _request_human_approval) instead of re-implementing them?
- Reuse: does it reuse base bricks (router, usage, audit, approval) rather than duplicating logic?
- Base purity: is any agent-specific logic wrongly leaking into the base, or vice versa?

## 3. Cohesion & correctness of logic
- Data flow: trace the main flow end-to-end — does it make sense, with no dead ends or unreachable branches?
- Field-name contracts: do field names match across models that interact (e.g. usage/audit/approval vs the mixin)?
- Manifest: are depends correct and complete? Are data files loaded in the right order (security before views)?
- Edge cases: missing records, empty results, network failure (LLMRouterError) handled cleanly? Compute methods safe when related models are empty?
- Cron: schedules sane, idempotent, and cap-aware?

## 4. Odoo 19 correctness
- Does all security/view/model XML follow the verified Odoo 19 patterns in rules.md? Flag any deprecated construct.

## 5. Improvements & tech debt
- Duplication that should be factored into the base or a shared helper.
- Logic that belongs in the base (so future agents reuse it) but currently lives in this agent.
- Readability/maintainability concerns that will multiply across the other agents.

## Output format
Give a short summary line first (e.g. "3 critical, 5 should-fix, 4 nice-to-have"), then the findings grouped by the 5 sections above, each as a bullet with severity + location + suggestion. End with a "Top 3 things I'd fix first" list.

Do NOT modify any code or Notion. This is a read-only review. After the report, ask me which findings I want you to implement.

## 6. Does this logic actually make sense? (Be brutally honest)
Step back from "is the code correct" and ask "is what we built actually GOOD and USEFUL." I want honest, critical judgment here — not reassurance. If something is over-engineered, pointless, or won't work in the real Saudi sales context, SAY SO plainly.

- Real-world usefulness: will this module actually help an Era Group salesperson/manager in practice, or is it theoretically nice but useless day-to-day? Give a concrete example of someone using it.
- Is the approach sound? Is there a simpler or fundamentally better way to achieve the same goal? Are we solving a real problem or an imagined one?
- Over-engineering: is any part more complex than the value justifies? What could be cut entirely without real loss?
- Hidden assumptions that may not hold: e.g. data quality, consent coverage, WhatsApp/waha reliability, LLM output quality in Arabic, cost realism. Flag assumptions that, if false, make the module worthless.
- Cost vs value: given the hard cost cap, is the LLM spend on this logic worth the outcome? Any call that burns budget for little gain?
- The honest verdict: in 2–3 sentences, would you actually ship this logic as-is to a paying Saudi client? If not, why not?

Rules for this section:
- Be direct and specific. "This is fine" is not acceptable unless you genuinely cannot find a single concern — and say why.
- It is BETTER to raise a concern I dismiss than to stay silent and let a bad design ship.
- Don't soften findings to be polite. I want the truth, even if it means rework.
- If the whole module's premise is questionable, say that too — don't just review the parts.
