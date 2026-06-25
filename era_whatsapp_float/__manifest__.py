{
    "name": "ERA WhatsApp Floating Discuss",
    "summary": "Floating, draggable, minimizable Discuss window focused on WhatsApp conversations.",
    "description": """
ERA WhatsApp Floating Discuss
=============================

Adds a floating, draggable and minimizable window that embeds the standard
Odoo Discuss experience, scoped to WhatsApp conversations (the official
``whatsapp`` module channels, ``channel_type = 'whatsapp'``).

* Launched from a WhatsApp icon in the top systray. Click to open the floating
  window; click again to minimize.
* The window shows a WhatsApp-only conversation list on the side and the real
  Discuss thread + composer (history, attachments, delivery ticks, ...) in the
  main pane.
* Minimize (chevron-down) or close hides the window; reopen it from the systray
  icon. A header toggle shows/hides the member list at a fixed width.
* Visible to WhatsApp users only (the WhatsApp security group).

The window reuses the genuine Discuss OWL components, so it stays in sync with
the rest of Odoo and inherits every Discuss feature automatically.
""",
    "version": "19.0.1.0.0",
    "category": "Discuss",
    "author": "Era Group",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    # 'whatsapp' (Enterprise) guarantees the WhatsApp Discuss category exists and
    # makes this module load *after* it, so `store.discuss.whatsapp` is defined.
    "depends": ["mail", "web", "whatsapp"],
    "data": [],
    "assets": {
        "web.assets_backend": [
            "era_whatsapp_float/static/src/whatsapp_float.scss",
            "era_whatsapp_float/static/src/whatsapp_float.js",
            "era_whatsapp_float/static/src/whatsapp_float.xml",
            "era_whatsapp_float/static/src/whatsapp_float_systray.js",
            "era_whatsapp_float/static/src/whatsapp_float_systray.xml",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
