# -*- coding: utf-8 -*-
{
    "name": "ERA CRM AI Agents — Base",
    "version": "19.0.4.0.0",
    "category": "CRM",
    "summary": "Shared infrastructure for the Era CRM AI Agents suite",
    "description": (
        "Foundation module for the Era Group CRM AI Agents suite. "
        "Provides the shared building blocks every agent inherits: the agent "
        "registry, model catalog, the crm.ai.agent.mixin abstract model, "
        "consumption tracking with a hard limit, the critical audit log, the "
        "human approval layer, and the AI Compliance Guard.\n\n"
        "LLM work runs through Odoo 19 native AI (OpenAI/Google), or through the "
        "Claude CLI / subscription transport provided by era_ai_accounts. The AI "
        "Compliance Guard is monkeypatched onto the native AI service and sits "
        "OUTERMOST on the public request_llm, so it enforces — pre-flight, before "
        "any prompt leaves (incl. before the CLI subprocess) — PDPL consent + "
        "record-driven PII redaction, the hard consumption limit (Rule 14: dollar "
        "cost cap on the priced API path, estimated-token limit on the CLI path), "
        "env-only secrets (Rule 03), and the persistent audit (Rule 20).\n\n"
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
        # Claude CLI / subscription transport. Declared so it loads BEFORE this
        # module — which means our guard patches LAST and is therefore OUTERMOST
        # on every shared LLMApiService method (request_llm, get_embedding). The
        # mixin also references era.ai.account for the CLI transport.
        "era_ai_accounts",
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
