{
    "name": "ERA WhatsApp Patch",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "summary": "Non-invasive patches for sadeem_waha_whatsapp warnings without modifying vendor code",
    "author": "Era Group",
    "license": "LGPL-3",
    "depends": ["sadeem_waha_whatsapp"],
    "data": [],
    "assets": {
        "web.assets_backend": [
            "era_waha_patch/static/src/js/wizard_view_chat_button_patch.js",
            "era_waha_patch/static/src/js/whatsapp_notification_service.js",
        ],
    },
    "installable": True,
    "application": False,
}
