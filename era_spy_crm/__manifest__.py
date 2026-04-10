# -*- coding: utf-8 -*-
{
    "name": "ERA EraSpy CRM",
    "version": "19.0.1.0.0",
    "category": "CRM",
    "summary": "EraSpy enrichment for CRM leads",
    "icon": "static/description/icon.png",
    'images': ["static/description/icon.png"],
    "description": (
        "EraSpy CRM adds lead enrichment from the EraSpy service. Users can enrich leads "
        "directly from the CRM form or list, sending identifiers such as LinkedIn, phone, "
        "and email to the API. Callback processing is handled directly in the controller "
        "with results applied immediately, while respecting rate limits and bulk caps."
    ),
    "author": "Era Group",
    "depends": ["era_spy_base", "crm", "contacts", "web"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/crm_lead_views.xml",
        "views/eraspy_callback_queue_views.xml",
        "views/wizard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "era_spy_crm/static/src/components/*.js",
            "era_spy_crm/static/src/components/*.xml",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
