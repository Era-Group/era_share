# -*- coding: utf-8 -*-
{
    'name': "Era Crm Forsah & Etimad For Clients",
    'version': '19.0.1.4.3',
    'category': 'CRM',
    'summary': 'Forsah studyable tenders & Etimad tenders pipeline for CRM',
    'description': """
Era CRM Forsah & Etimad for Clients
===================================

Imports tender opportunities from the Forsah and Etimad portals and presents
them as a triage pipeline of studyable tenders:

* Forsah feed upserts (triage status, tags and linked opportunities survive a refresh).
* "Studyable" triage workflow (To Review / Studyable / Not Suitable / Converted).
* Search filters and group-by on both tender lists.
* One-click conversion of a tender into a CRM opportunity.
* AI business-match: scores each open tender's relevance to the company's
  business description (set in Settings) and surfaces the best-fitting tenders.
  Uses Odoo's standard AI agent (ai.agent) — soft dependency on the `ai` app.
    """,
    'author': 'Era group',
    'email': 'aqlan@era.net.sa',
    'website': 'https://era.net.sa',
    'license': 'AGPL-3',
    'depends': ['base', 'contacts', 'crm', 'mail', 'utm'],
    'data': [
        'security/ir.model.access.csv',
        'views/crm_forsah_view.xml',
        'views/crm_etimad_view.xml',
        'views/res_config_settings_view.xml',
        'data/cronjob.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_crm_forsah_client/static/src/css/styles.css',
        ],
    },
    'installable': True,
    'application': True,
}
