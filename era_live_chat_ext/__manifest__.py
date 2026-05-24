# -*- coding: utf-8 -*-
{
    "name": "ERA Live Chat Extensions",
    "version": "19.0.1.0.0",
    "category": "Website/Live Chat",
    "summary": "Fatoratec livechat UX tweaks: hide 'Ask Human' button + fix RTL horizontal overflow",
    "description": (
        "Front-end overrides for the Odoo Live Chat widget used on fatoratec.com:\n"
        "  - Hides the auto-rendered 'Ask Human / اسأل بشرياً' fallback button so the AI agent\n"
        "    handles all conversations end-to-end (no human handoff is staffed).\n"
        "  - Fixes a horizontal-scroll bug where long Arabic AI replies overflow the\n"
        "    chat panel instead of wrapping inside the message bubble.\n"
        "Install once; nothing to configure."
    ),
    "author": "Era Group",
    "website": "https://fatoratec.com",
    "depends": ["im_livechat", "website"],
    "data": [],
    "assets": {
        "web.assets_frontend": [
            "era_live_chat_ext/static/src/scss/livechat_overrides.scss",
            "era_live_chat_ext/static/src/js/hide_ask_human.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "license": "LGPL-3",
}
