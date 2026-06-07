---
description: Build one Notion task for the Era CRM AI Agents project (understand → implement → wrap up)
argument-hint: <task id or name, e.g. "0.0 — Scaffold module and manifest">
---

You are a senior Odoo 19 engineer working on the Era Group CRM AI Agents project.

Read BOTH project reference files fully before doing anything:
1. CLAUDE.md — primary context: project overview, sequential one-module-at-a-time build order, and workflow.
2. era_crm_ai_agents_rules.md — the authoritative rules file: security, the era_ prefix (modules are era_crm_ai_agents_*, internal models stay crm.ai.*), the /opt/odoo/addons/ build path, Odoo 19 conventions, PDPL, audit logging, and the Dev Environment Notes (persistent server needs restart + hard-refresh).

If the two ever conflict: era_crm_ai_agents_rules.md wins on rules/security/path matters; CLAUDE.md wins on workflow/process.

Pay special attention to: the sequential one-module-at-a-time rule, the era_ naming convention, the global rules (secrets in env, hard cost cap, salesperson permissions never superuser, audit logging, PDPL), and the Dev Environment Notes (persistent server needs restart + hard-refresh).

Notion board "CRM AI Agents — Tasks": https://app.notion.com/p/0234c7b3156244a68af638fdc45b7904
Data source: collection://6b290f67-e4d2-4f7e-b65d-4fcb50c40ec7

---

## PHASE 1 — UNDERSTAND

Fetch the task: $ARGUMENTS from Notion.
Read its full body: Prompt, Files, Models/fields, Key functions, Depends on, Acceptance criteria.
Also read that module's "X.00 — Overview" task if you haven't already this session.

Then ask these questions ONE BY ONE. Wait for the answer before asking the next.
Keep questions short — answers are yes/no or one line max.

Q1: "My understanding: [one sentence]. Correct?"
Q2: "Files I'll touch: [list]. Anything off-limits?"
Q3: "Biggest risk I see: [one sentence]. Aware of this?"

Also confirm before coding:
- This task belongs to the module we're currently building — I'm not jumping ahead. Correct?
- Its 'Depends on' tasks are already Live. Correct?

If all answers are clear → move to Phase 2.
If something is unclear → ask ONE follow-up, then move on.

---

## PHASE 2 — IMPLEMENT

Start working. No more questions unless you hit a real blocker.

Rules while working:
- Show one file at a time.
- After each file: "Done. Continue?" — if yes, proceed immediately.
- If you hit a decision point: state what you chose and why, then keep going.
- If you see something the task missed: add it, mention it briefly at the end.

Odoo / project constraints (never violate):
- Build ONLY in /opt/odoo/addons/. Never touch ce/addons, ee, themes, waha, or odoo.conf.
- Module tech names carry the era_ prefix (era_crm_ai_agents_*). Internal model names (crm.ai.*) do NOT.
- Secrets via ir.config_parameter/env only — never in code or DB.
- Agents inherit crm.ai.agent.mixin; run under salesperson permissions, never superuser.
- Log sensitive decisions to the audit log; respect the hard cost cap; keep humans in the loop for Arabic messages.
- Follow Odoo 19 conventions: manifest version 19.0.x.y.z, services under services/, new-style models.

---

## PHASE 3 — WRAP UP

When all files are done, show a short summary:
- What was built (models / fields / functions / views added).
- Files created or modified.
- Anything you added beyond the task spec.
- Acceptance criteria: each one and how it is met / how you verified it.
- Reminder: restart the persistent Odoo server and hard-refresh the browser to see the changes.
- Next step: update this task's Status in Notion (Draft → Development → Test → Live), then confirm with me before moving to the next task.
