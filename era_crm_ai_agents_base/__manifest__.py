# -*- coding: utf-8 -*-
{
    "name": "ERA CRM AI Agents — Base",
    "version": "19.0.3.0.0",
    "category": "CRM",
    "summary": "Shared infrastructure for the Era CRM AI Agents suite",
    "description": (
        "Foundation module for the Era Group CRM AI Agents suite. "
        "Provides the shared building blocks every agent inherits: the agent "
        "registry, model catalog, the crm.ai.agent.mixin abstract model, cost "
        "tracking with a hard cost cap, the critical audit log, the human "
        "approval layer, and the AI Compliance Guard.\n\n"
        "All LLM work runs through Odoo 19 native AI (OpenAI/Google only). The "
        "AI Compliance Guard is monkeypatched onto the native AI service to "
        "enforce, pre-flight: PDPL consent + record-driven PII redaction, the "
        "hard cost cap (Rule 14), env-only secrets (Rule 03), and the persistent "
        "audit (Rule 20).\n\n"
        "This is module 0 — pure infrastructure with no agent-specific business "
        "logic. Every other module in the suite declares this module in its "
        "manifest 'depends'. Built first; nothing else installs until it is "
        "complete and tested."
    ),
    "author": "Era Group",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "crm",
        # Odoo 19 native AI (EE). The guard monkeypatches its LLMApiService and
        # ir.actions.server._ai_action_run; the mixin calls request_llm.
        "ai",
    ],
    "data": [
        # Security FIRST — groups/ACL must exist before any view references them.
        "security/crm_ai_agents_groups.xml",
        "security/ir.model.access.csv",
        "security/crm_ai_agent_rules.xml",
        # Views
        "views/crm_ai_agent_views.xml",
        "views/crm_ai_model_views.xml",
        "views/crm_ai_usage_views.xml",
        "views/crm_ai_audit_log_views.xml",
        "views/crm_ai_approval_views.xml",
        "views/crm_ai_dashboard_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        # Menus last — all actions must be defined before they are referenced.
        "views/crm_ai_agents_menus.xml",
        # Data
        "data/crm_ai_model_data.xml",
        "data/ir_config_parameter_data.xml",
        "data/ir_cron_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
