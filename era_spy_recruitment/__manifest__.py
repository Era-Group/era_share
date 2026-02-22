# -*- coding: utf-8 -*-
{
    "name": "ERA EraSpy Recruitment",
    "version": "19.0.1.0.0",
    "category": "Human Resources",
    "summary": "Enrich applicants via EraSpy and AI matching",
    "icon": "static/description/icon.png",
    'images': ["static/description/icon.png"],
    "description": (
        "EraSpy Recruitment enriches applicants with data returned from EraSpy. It supports "
        "bulk enrich actions, callback queue processing, and optional AI qualification matching "
        "against the job description. The module sends LinkedIn, phone, and email identifiers "
        "and applies results back to the applicant record when callbacks arrive."
    ),
    "author": "Era Group",
    "depends": ["era_spy_base", "hr_recruitment", "web"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/ir_config_parameter.xml",
        "views/res_config_settings_views.xml",
        "views/hr_applicant_views.xml",
        "views/eraspy_applicant_callback_queue_views.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
