# Part of Era Group custom addons.
{
    'name': 'Era WAHA WhatsApp Integration',
    'version': '19.0.1.15.0',
    'category': 'Productivity/WhatsApp',
    'summary': 'Two-way WAHA (WhatsApp Web) messaging inside Odoo Discuss',
    'description': """
Era WAHA WhatsApp Integration
=============================
Plugs the WAHA (https://waha.devlike.pro) unofficial WhatsApp HTTP API into
Odoo's standard Enterprise WhatsApp/Discuss stack as an alternative backend
"provider". Inbound WAHA messages appear as ``channel_type='whatsapp'`` Discuss
channels linked to the partner sharing the sender's phone number, and replies
typed in Discuss are delivered through WAHA. Includes session/QR management,
delivery/read ticks and a smart historical-conversation backfill.
""",
    'author': 'Era Group',
    'website': 'https://era.net.sa',
    'license': 'LGPL-3',
    'depends': ['whatsapp'],
    'data': [
        'security/waha_security.xml',
        'data/ir_cron_data.xml',
        'data/ir_actions_server_data.xml',
        'views/whatsapp_account_views.xml',
        'views/whatsapp_composer_views.xml',
        'views/mail_compose_message_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_waha_integration/static/src/**/*',
        ],
        # Only the Discuss sidebar-category patches are public-safe; the floating
        # window (static/src/float/*) is backend-only and must not enter this bundle.
        'mail.assets_public': [
            'era_waha_integration/static/src/core/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
