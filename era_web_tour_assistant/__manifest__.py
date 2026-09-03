# -*- coding: utf-8 -*-
{
    "name": "Tour Assistant",
    "version": "19.0.1.0.0",
    "category": "Productivity",
    "summary": "Users ask how to do something and get walked through it by a tour.",
    "description": """
Tour Assistant
==============

A user who does not know how to do something types the question where they
are, and the assistant walks them through it.

Every question becomes a ``tour.assistant.request``. The assistant first looks
for a published tour that answers it and starts that tour straight away. A
question nothing answers is queued instead, so the queue is a ranked list of
what the staff could not work out on their own — which is exactly the list of
tours worth building next.

The module knows nothing about any particular business module: it matches on
the tours present in the database.
""",
    "author": "Era Group",
    "maintainer": "Ahmed Aqlan",
    "email": "info@era.net.sa",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    "depends": ["web_tour", "base_setup"],
    "data": [
        "security/tour_assistant_security.xml",
        "data/tour_assistant_cron.xml",
        "security/ir.model.access.csv",
        "views/web_tour_tour_views.xml",
        "views/tour_request_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "era_web_tour_assistant/static/src/scss/tour_assistant.scss",
            "era_web_tour_assistant/static/src/js/tour_assistant_dialog.js",
            "era_web_tour_assistant/static/src/js/tour_assistant_systray.js",
            "era_web_tour_assistant/static/src/js/tour_assistant_skip.js",
            "era_web_tour_assistant/static/src/xml/tour_assistant_templates.xml",
        ],
    },
    "application": True,
    "installable": True,
}
