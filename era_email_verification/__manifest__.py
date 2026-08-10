{
    "name": "ERA Email Verification",
    "summary": "Check email addresses, see clear results, and keep invalid "
               "addresses out of your mailings.",
    "description": """
ERA Email Verification
======================

Check contact and mailing-list email addresses before sending.

* Verify one address from the contact form or check many addresses together.
* See a clear result: deliverable, risky, undeliverable, or unknown.
* View the score and reason directly on the contact.
* Keep selected results out of marketing mailings automatically.
* Re-check addresses when they change or become old.

Large lists are checked safely in the background, so users can keep working.
The module connects only to your private, administrator-configured verifier.
""",
    "author": "ERA",
    "website": "https://letsw.com",
    "category": "Marketing/Email Marketing",
    "version": "19.0.1.5.3",
    "icon": "/era_email_verification/static/description/icon.png",
    "license": "LGPL-3",
    "depends": ["mail", "contacts", "mass_mailing"],
    "data": [
        "security/email_verification_groups.xml",
        "security/ir.model.access.csv",
        "security/email_verification_rules.xml",
        "data/assign_admin_groups.xml",
        "data/ir_config_parameter_data.xml",
        "data/ir_cron_data.xml",
        "wizards/verify_email_wizard_views.xml",
        "views/email_verification_batch_views.xml",
        "views/email_verification_item_views.xml",
        "views/res_partner_views.xml",
        "views/mailing_contact_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    # Not a standalone app: the menus live inside Email Marketing.
    "application": False,
}
