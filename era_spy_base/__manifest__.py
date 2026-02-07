# -*- coding: utf-8 -*-
{
    "name": "ERA EraSpy Base",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "summary": "Shared EraSpy client and settings",
    'images': ["static/description/icon.png"],
    "icon": "static/description/icon.png",
    "description": (
        "EraSpy Base provides the shared API client, configuration settings, and backend assets "
        "used by all EraSpy add-ons. It manages the proxy/base URL, rate-limit controls, and "
        "callback endpoints used to receive asynchronous results. Install this module first, "
        "then add CRM or Recruitment extensions as needed."
    ),
    "author": "Era Group",
    "post_load": "post_load",
    "depends": ["base", "base_setup", "web"],
    "data": [
        "views/menus.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "era_spy_base/static/src/js/eraspy_console_log.js",
            "era_spy_base/static/src/scss/eraspy_backend.scss",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
