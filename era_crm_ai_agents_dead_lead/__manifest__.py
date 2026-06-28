# -*- coding: utf-8 -*-
{
    "name": "ERA CRM AI Agents — Dead-Lead Resurrection",
    "version": "19.0.1.8.0",
    "category": "CRM",
    "summary": "Resurrects closed-lost leads: detects a fresh trigger, drafts an "
               "approved Arabic comeback, routes to the salesperson for approval",
    "description": (
        "Module 3 of the Era Group CRM AI Agents suite — the Dead-Lead "
        "Resurrection Agent, strategic bet #1 and the first true *sending* "
        "agent.\n\n"
        "It periodically scans closed-lost leads, classifies them by loss "
        "reason, detects a fresh trigger (champion moved, the old price "
        "objection resolved, enough time elapsed), uses AI to fill ONLY the "
        "personal parts of a manager-approved Arabic template, then passes "
        "through the cross-cutting Compliance layer (PDPL consent, send "
        "windows, cultural norms) and the Base human-approval gate before any "
        "message is sent via WhatsApp.\n\n"
        "Because it sends, it depends on both the shared Base (agent registry, "
        "mixin, audit log, approval layer, consumption control, AI Compliance "
        "Guard) and the Compliance layer. Every LLM call runs through "
        "crm.ai.agent.mixin._call_llm under salesperson permissions (Rule 09 / "
        "19) — never superuser — and every sensitive decision is logged to the "
        "Base critical audit log (Rule 20).\n\n"
        "This task (3.0) is the scaffold only: an empty, cleanly-installable "
        "skeleton on top of era_crm_ai_agents_base + era_crm_ai_agents_"
        "compliance. The agent model, classification/trigger engine, template "
        "model, security, views and data are filled in by the subsequent 3.x "
        "tasks."
    ),
    "author": "Era Group",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    "depends": [
        # The shared foundation: agent registry, crm.ai.agent.mixin, audit log,
        # approval layer, consumption control and the AI Compliance Guard.
        "era_crm_ai_agents_base",
        # The cross-cutting compliance guard (PDPL consent, prayer/Hijri send
        # windows, cultural norms). A HARD prerequisite for every sending agent;
        # Dead-Lead is the first one, so it must be installed before this.
        "era_crm_ai_agents_compliance",
    ],
    "data": [
        # Security FIRST — ACL must load before the data records reference the
        # models, and before any view. Record-rules file documents the Rule-09
        # posture (own lost leads only; see the file header).
        "security/ir.model.access.csv",
        "security/dead_lead_rules.xml",
        # Data: pre-seed the agent registry row, its config record, and the
        # default approved template.
        "data/crm_ai_dead_lead_agent_data.xml",
        # Slotted, manager-approved comeback templates (task 3.4). The agent
        # fills only the {{slots}}; the body stays template-bound.
        "data/templates.xml",
        # Daily scan cron (task 3.7) — runs under the configurable least-
        # privilege identity; inert until the agent's scan_enabled is ON.
        "data/ir_cron.xml",
        # Views (task 3.8) — actions/views before the menus that reference them.
        "views/crm_ai_dead_lead_agent_views.xml",
        "views/crm_ai_dead_lead_template_views.xml",
        "views/crm_lost_reason_views.xml",
        "views/crm_lead_views.xml",
        "views/dead_lead_approval_views.xml",
        # Menus LAST — all actions must be defined before they are referenced.
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
