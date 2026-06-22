# -*- coding: utf-8 -*-
{
    "name": "ERA CRM AI Agents — Lead Generation",
    "version": "19.0.1.0.0",
    "category": "CRM",
    "summary": "B2B prospecting engine that brings net-new companies and decision-makers into the Era CRM AI Agents suite",
    "description": (
        "Module 16 of the Era Group CRM AI Agents suite — the prospecting / "
        "lead-generation engine at the HEAD of the pipeline.\n\n"
        "Unlike Enrichment (#2), which only completes records that already "
        "exist, Lead Gen brings NEW B2B companies (res.partner type=company) "
        "and their decision-makers (type=individual, linked via parent_id) INTO "
        "the system from external sources. Every created record is tagged "
        "'by_lead_generator_agent' and stamped with the exact source provider, "
        "so externally-sourced records can be isolated or purged in one move. "
        "New records are de-duplicated against existing partners, then handed to "
        "Enrichment (#2) to complete/verify and Compliance (#1) to guard before "
        "any outreach.\n\n"
        "Sources run through a waterfall gated by three independent checks: a "
        "manual 'active' toggle, presence of the source's env-var token (name "
        "only — Rule 03), and PDPL legal permission. The whole module is gated "
        "behind a master toggle (disabled by default); decision-maker (people) "
        "fetching is a separate toggle, also off by default — the heaviest part "
        "under PDPL. Cost tracking (Rule 14) counts BOTH the source-API call and "
        "any LLM-extraction call; every decision is written to the Base audit "
        "log (Rule 20). Runs under salesperson permissions, never superuser.\n\n"
        "This task (16.0) is the scaffold only: an empty, cleanly-installable "
        "skeleton on top of era_crm_ai_agents_base. Models, sources, security, "
        "data and views are filled in by the subsequent 16.x tasks. Integration "
        "with Enrichment (#2) and Compliance (#1) is via runtime tags/calls, so "
        "neither is declared as a hard dependency here."
    ),
    "author": "Era Group",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    "depends": [
        # The shared foundation: agent registry, mixin, audit log, approval
        # layer, consumption control and the AI Compliance Guard. Every agent
        # module in the suite declares the Base here. Enrichment (#2) and
        # Compliance (#1) are intentionally NOT depended on — the hand-off is a
        # runtime concern (tags + guard calls), not a hard import.
        "era_crm_ai_agents_base",
    ],
    # The single non-LLM egress seam (crm.ai.lead_gen.agent._http_get) uses
    # ``requests``; declare it so a misprovisioned host fails fast at install.
    "external_dependencies": {"python": ["requests"]},
    "data": [
        # 16.2 — security FIRST: who can run a fetch (AI Agents User, read-only
        # on sources) vs. who manages providers/tokens/PDPL (AI Agents Manager).
        # Reuses the Base groups; ACL loads before any record references the model.
        "security/ir.model.access.csv",
        "security/crm_ai_lead_gen_rules.xml",
        # 16.3 — seed every Lead-Gen config param so OFF toggles persist on a
        # clean install (first-OFF-lost fix).
        "data/ir_config_parameter_data.xml",
        # 16.4 — pre-seed the Lead-Gen agent's crm.ai.agent registry row.
        "data/crm_ai_lead_gen_agent_data.xml",
        # 16.5 — the provenance tag stamped on created partner records.
        "data/ir_model_data.xml",
        # 16.1 — seed every candidate lead-gen source (all active=False).
        "data/crm_ai_lead_gen_provider_data.xml",
        # 16.3 — manager-only Lead Generation Settings block.
        "views/res_config_settings_views.xml",
        # 16.8 — scheduled run (inert via the master toggle; runs as the
        # dedicated non-superuser Lead Generation Bot).
        "data/ir_cron.xml",
        # 16.9 — views + manager-only menus (menus last: they ref the actions).
        "views/crm_ai_lead_gen_provider_views.xml",
        "views/crm_ai_lead_gen_partner_views.xml",
        "views/crm_ai_lead_gen_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
