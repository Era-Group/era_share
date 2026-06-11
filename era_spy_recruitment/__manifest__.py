# -*- coding: utf-8 -*-
{
    "name": "ERA Era Enrich Recruitment",
    "version": "19.0.1.0.0",
    "category": "Human Resources",
    "summary": "Enrich applicants via Era Enrich and AI matching",
    "icon": "static/description/icon.png",
    'images': ["static/description/icon.png"],
    "description": (
        "Era Enrich Recruitment enriches applicants with data returned from Era Enrich. It supports "
        "bulk enrich actions, callback queue processing, and optional AI qualification matching "
        "against the job description. The module sends LinkedIn, phone, and email identifiers "
        "and applies results back to the applicant record when callbacks arrive."
    ),
    "author": "Era Group",
    "depends": ["era_spy_base", "hr_recruitment", "web"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
        "views/hr_applicant_views.xml",
        "views/eraspy_applicant_callback_queue_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "era_spy_recruitment/static/src/components/*.js",
            "era_spy_recruitment/static/src/components/*.xml",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
