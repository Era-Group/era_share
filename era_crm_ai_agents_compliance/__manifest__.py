# -*- coding: utf-8 -*-
{
    "name": "ERA CRM AI Agents — Compliance",
    "version": "19.0.1.0.0",
    "category": "CRM",
    "summary": "Cross-cutting PDPL / send-window / cultural compliance guard for the Era CRM AI Agents suite",
    "description": (
        "Module 1 of the Era Group CRM AI Agents suite — the cross-cutting "
        "compliance layer, NOT a standalone agent.\n\n"
        "Every sending agent passes through this layer before any message goes "
        "out. It manages PDPL consent (explicit consent before any marketing "
        "message, opt-out within 72h, DSAR access/erasure), adjusts send windows "
        "for the Hijri calendar and prayer times, and enforces cultural discourse "
        "norms. It permits or blocks each send and logs every decision to the "
        "Base critical audit log (Rule 20), running under salesperson permissions "
        "(Rule 09 / 19) — never superuser.\n\n"
        "This is a hard prerequisite for every sending agent (Dead-Lead, Inbound "
        "Qualifier, Action WhatsApp, etc.). Built immediately after the Base so no "
        "agent has to be re-architected later.\n\n"
        "This task (1.0) is the scaffold only: an empty, cleanly-installable "
        "skeleton on top of era_crm_ai_agents_base. The guard service, models, "
        "security and data are filled in by the subsequent 1.x tasks."
    ),
    "author": "Era Group",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    "depends": [
        # The shared foundation: agent registry, mixin, audit log, approval
        # layer, consumption control and the AI Compliance Guard. Every agent
        # module in the suite declares the Base here.
        "era_crm_ai_agents_base",
    ],
    "data": [
        # Security FIRST — ACL/record rules must load before any view.
        "security/ir.model.access.csv",
        "security/crm_ai_consent_rules.xml",
        # Data
        "data/ir_cron_data.xml",
        # Views
        "views/crm_ai_consent_views.xml",
        "views/crm_ai_dsar_wizard_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        # Menus last — all actions must be defined before they are referenced.
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
