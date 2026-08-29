{
    "name": "Era AI Business Manager",
    "summary": "An AI manager that studies your business, then runs its customer follow-up, support replies and campaigns under hard guardrails",
    "description": """
Era AI Business Manager
=======================

Gives any Odoo database an autonomous manager: it studies what the business
actually is, writes its own brief and playbooks to match, then watches the
customers, answers what arrives and follows up what goes quiet -- while every
customer-facing message passes one audited queue with guardrails enforced in
code rather than in a prompt.

* Business discovery: collects hard evidence from the database (apps in use,
  record volumes, languages, activity) and has the AI turn it into a manager
  brief, watchlists and playbooks fitted to this trade -- reviewed by a human
  before anything takes effect.
* Watchlists: any model, any domain, any priority. What counts as a customer
  worth chasing is configuration, not code.
* One outreach queue for every outbound message, with frequency caps,
  deduplication, opt-out and send windows enforced in Python.
* Gradual autonomy: approve everything for a while, then let it run, on a date
  you choose.
* A deterministic watchdog that keeps working when the AI does not.

Business-agnostic by design: no dependency on any particular industry module.
""",
    "version": "19.0.1.5.2",
    "category": "Productivity",
    "author": "Era Group",
    "email": "info@era.net.sa",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "depends": [
        "base",
        "mail",
        "contacts",
        "mass_mailing",
        "digest",
        "aidoo",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter_data.xml",
        "data/ai_staff_data.xml",
        "data/ir_cron_data.xml",
        "views/profile_views.xml",
        "views/dashboard_views.xml",
        "views/conversation_views.xml",
        "views/watchlist_views.xml",
        "views/watchlist_compose_views.xml",
        "views/outreach_views.xml",
        "views/watchdog_alert_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "images": ["static/description/banner.png"],
}
