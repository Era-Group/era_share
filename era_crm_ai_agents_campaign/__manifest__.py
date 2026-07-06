# -*- coding: utf-8 -*-
{
    "name": "ERA CRM AI Agents — Campaign Agent",
    "version": "19.0.1.0.2",
    "category": "Marketing",
    "summary": "Daily AI campaign builder: selects customers, matches services via "
               "LLM, drafts per-customer emails, and hands approved campaigns to "
               "Email Marketing",
    "description": (
        "Module 17 of the Era Group CRM AI Agents suite — the daily Campaign "
        "Agent.\n\n"
        "Each day the agent builds one or more campaigns inside its own models: "
        "it selects suitable customers fail-closed (suppression list, per-partner "
        "cooldown, monthly frequency cap, PDPL consent), matches each customer to "
        "exactly one ACTIVE service from the manager-curated service catalog via "
        "the LLM (grounded — the model may never invent a service), drafts a "
        "per-customer subject and body in the customer's language, routes the "
        "result through human approval, and — as the FINAL step — hands approved "
        "campaigns to Odoo's official Email Marketing (mailing.mailing) to "
        "actually send.\n\n"
        "ALL LLM calls run through the Base egress seam "
        "(crm.ai.agent.mixin._call_llm under the AI Compliance Guard) — this "
        "module contains no direct LLM/HTTP imports. Two manager-selectable "
        "transports: 'token' (native API, env-only key) or 'era_ai_accounts' "
        "(editor-subscription CLI transport, runtime-optional, FAIL CLOSED when "
        "unavailable). PDPL data-minimization sends the LLM business attributes "
        "only under an opaque partner_ref — never name/email/phone; real PII is "
        "merged into the body locally, after the LLM returns. A master PDPL "
        "toggle (default ON) governs minimization AND consent checks; disabling "
        "it is an explicit, documented operator decision (see "
        "docs/MANAGER_MANUAL.md).\n\n"
        "Integration with the Compliance layer (#1) and era_ai_accounts is via "
        "runtime registry checks — neither is a hard dependency. The scheduled "
        "run resolves its identity via the suite-wide cron resolver "
        "(crm.ai.agent._get_cron_run_user), defaulting to the least-privilege "
        "CRM AI Automation user (Rule 09)."
    ),
    "author": "Era Group",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    "depends": [
        # The shared foundation: agent registry, mixin (the single LLM egress
        # seam), audit log, approval layer, consumption control and the AI
        # Compliance Guard. Compliance (#1) and era_ai_accounts are intentionally
        # NOT depended on — both are resolved at runtime via registry checks and
        # the module fails closed when a selected optional piece is absent.
        "era_crm_ai_agents_base",
        # The official Email Marketing app is the FINAL hand-off target
        # (mailing.mailing) — an explicit, spec-mandated hard dependency.
        "mass_mailing",
    ],
    "data": [
        # Security FIRST: groups, then ACLs, then the documented rule file.
        "security/crm_ai_campaign_security.xml",
        "security/ir.model.access.csv",
        # Seed every config param so OFF toggles persist on a clean install
        # (first-OFF-lost fix) — booleans as 'True'/'False' strings.
        "data/ir_config_parameter_data.xml",
        # Pre-seed the Campaign agent's crm.ai.agent registry row.
        "data/crm_ai_campaign_agent_data.xml",
        # Manager-only Campaign Settings block (own app, sibling of the Base).
        "views/res_config_settings_views.xml",
        # Views for the working models + configuration catalogs, menus last.
        "views/crm_ai_campaign_views.xml",
        "views/crm_ai_campaign_config_views.xml",
        "views/crm_ai_campaign_menus.xml",
        # Scheduled daily run (inert via the master toggle; identity resolved
        # suite-wide at run time). New crons need a full server restart.
        "data/ir_cron_data.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
