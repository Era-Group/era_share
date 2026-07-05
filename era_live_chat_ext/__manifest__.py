# -*- coding: utf-8 -*-
{
    "name": "ERA Live Chat Extensions",
    "version": "19.0.2.1.7",
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
        "views/support_page_branding.xml",
    ],
    # Register once in the shared livechat *core* bundle. In Odoo 19 there is
    # no `im_livechat.assets_embed` bundle (the old target was a silent no-op).
    # `im_livechat.assets_embed_core` is `('include')`-ed into BOTH:
    #   - `web.assets_frontend`            → the button + floating panel on
    #                                        plain website pages (e.g. the home
    #                                        page), and
    #   - `im_livechat.assets_embed_external` (and `_cors`) → the standalone
    #                                        `/im_livechat/support/<id>` page and
    #                                        cross-origin embeds.
    # Putting our overrides in core is what makes them apply on the support
    # page too — previously the "Ask a Human" button was only hidden on the
    # frontend and stayed visible at /im_livechat/support/<id>.
    "assets": {
        "im_livechat.assets_embed_core": [
            "era_live_chat_ext/static/src/scss/livechat_overrides.scss",
            "era_live_chat_ext/static/src/js/hide_ask_human.js",
        ],
        # Backend Discuss (operators reviewing AI-managed conversations) —
        # SCSS only; core embed JS is not loaded in the backend web client.
        "mail.assets_messaging": [
            "era_live_chat_ext/static/src/scss/livechat_overrides.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "license": "LGPL-3",
}
