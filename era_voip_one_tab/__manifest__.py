{
    "name": "ERA VoIP One Tab",
    "summary": "Connect SIP only on call and keep a single registered tab.",
    "version": "1.0.0",
    "category": "Productivity/Phone",
    "author": "Era Group",
    "license": "LGPL-3",
    "depends": ["voip", "web"],
    "data": [
        "security/voip_one_tab_security.xml",
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "era_voip_one_tab/static/src/core/voip_one_tab.js",
        ],
    },
}
