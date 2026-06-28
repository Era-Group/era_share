# -*- coding: utf-8 -*-
{
    "name": "ERA CRM AI Agents — Enrichment",
    "version": "19.0.1.3.0",
    "category": "CRM",
    "summary": "Waterfall data-enrichment engine (email / phone / WhatsApp deliverability) for the Era CRM AI Agents suite",
    "description": (
        "Module 2 of the Era Group CRM AI Agents suite — the waterfall data-"
        "enrichment engine. It walks enrichment providers in priority order, "
        "STOPS at the first that returns data, fills partner fields from public "
        "sources, and verifies deliverability (email + mobile + WhatsApp "
        "presence). It is reusable DATA INFRASTRUCTURE: a brick other agents "
        "(notably Dormant Gold, #4) call to refresh stale records.\n\n"
        "Cost (Rule 14): every BILLABLE provider call is booked to the Base "
        "crm.ai.usage (per-provider billing mode per_call / per_success / free), "
        "with a per-attempt cap pre-check — not just the successful provider. "
        "Every direct provider call is a registered non-LLM egress through a "
        "single seam; tokens are env-only (Rule 03).\n\n"
        "WhatsApp presence is RUNTIME-OPTIONAL and double-gated: it activates "
        "only when the WAHA connector (sadeem_waha_whatsapp) is actually "
        "available AND a manager has explicitly enabled it AND per-record consent "
        "is positively confirmed (fail-closed). The connector is NOT a hard "
        "dependency — if it is absent the module still installs and runs "
        "(email + phone), with only the WhatsApp slot reporting 'unknown'.\n\n"
        "This task (2.0) is the SCAFFOLD only: an installable skeleton on top of "
        "era_crm_ai_agents_base — models, the uniform provider-handler interface "
        "(with the gated+optional WhatsApp slot), the single egress seam, the "
        "billing hook and the cap pre-check are wired but inert. Provider logic "
        "is filled in by the subsequent 2.x tasks."
    ),
    "author": "Era Group",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    # Base ONLY. The WAHA connector (sadeem_waha_whatsapp) and Compliance (#1)
    # are SOFT, runtime-optional integrations detected via the registry — never
    # hard depends, so this module installs/functions when either is absent.
    "depends": ["era_crm_ai_agents_base"],
    # The single non-LLM egress seam (crm.ai.enrichment.agent._http_get) uses
    # ``requests``; declared so a misprovisioned host fails fast at install.
    "external_dependencies": {"python": ["requests"]},
    "data": [
        # Security FIRST — ACL must load before any record references the models.
        "security/ir.model.access.csv",
        "security/crm_ai_enrichment_rules.xml",
        # Seed config params so OFF toggles persist on a clean install.
        "data/ir_config_parameter_data.xml",
        # Pre-seed the Enrichment agent's crm.ai.agent registry row.
        "data/crm_ai_enrichment_agent_data.xml",
        # Seed the default enrichment waterfall (all providers active=False).
        "data/crm_ai_enrichment_provider_data.xml",
        # Scheduled stale-record re-verification cron (inert until enabled).
        "data/ir_cron.xml",
        # Manager-only Enrichment settings block (own sibling app).
        "views/res_config_settings_views.xml",
        # Views + manager-only menus (menus last: they ref the actions).
        "views/crm_ai_enrichment_provider_views.xml",
        "views/crm_ai_enrichment_request_views.xml",
        "views/crm_ai_enrichment_attempt_views.xml",
        "views/res_partner_views.xml",
        "views/crm_ai_enrichment_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
