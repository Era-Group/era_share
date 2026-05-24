# -*- coding: utf-8 -*-
{
    "name": "ERA Live Chat Extensions",
    "version": "19.0.2.0.2",
    "category": "Website/Live Chat",
    "summary": "Fatoratec livechat UX tweaks: hide 'Ask Human' button + fix RTL horizontal overflow",
    "description": (
        "Front-end overrides + backend menu for the Odoo Live Chat widget used on\n"
        "fatoratec.com:\n"
        "  - Hides the auto-rendered 'Ask Human / اسأل بشرياً' fallback button so the AI agent\n"
        "    handles all conversations end-to-end (no human handoff is staffed).\n"
        "  - Fixes a horizontal-scroll bug where long Arabic AI replies overflow the\n"
        "    chat panel instead of wrapping inside the message bubble.\n"
        "  - Forces the chat to use the website's body font (keeps icons intact).\n"
        "  - Adds 'Live Chat → All Chat History' menu so AI-managed conversations\n"
        "    are visible alongside human-handled sessions.\n"
        "Install once; nothing to configure."
    ),
    "author": "Era Group",
    "website": "https://fatoratec.com",
    "depends": ["im_livechat", "website"],
    "data": [
        "views/livechat_history_views.xml",
    ],
    # Register in every bundle the livechat widget might mount under. Odoo
    # 19 renders the embedded popup against `im_livechat.assets_embed`; the
    # public-page widgets (the button + the floating panel on plain website
    # pages) load from `web.assets_frontend`. Listing in both is harmless —
    # the same file just gets included twice — and guarantees the rules
    # apply wherever the chat mounts.
    "assets": {
        "web.assets_frontend": [
            "era_live_chat_ext/static/src/scss/livechat_overrides.scss",
            "era_live_chat_ext/static/src/js/hide_ask_human.js",
        ],
        "im_livechat.assets_embed": [
            "era_live_chat_ext/static/src/scss/livechat_overrides.scss",
            "era_live_chat_ext/static/src/js/hide_ask_human.js",
        ],
        "mail.assets_messaging": [
            "era_live_chat_ext/static/src/scss/livechat_overrides.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "license": "LGPL-3",
}
