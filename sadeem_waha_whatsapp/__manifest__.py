{
    'name': 'WAHA WhatsApp Integration',
    'version': '19.0.1.260411',
    'category': 'Tools',
    'summary': 'WhatsApp Integration using WAHA API',
    'description': '''
        WhatsApp Integration Module using WAHA
        =====================================

        Features:
        - Manage WhatsApp sessions with QR code scanning
        - Send text messages, files, and voice messages
        - Set default WhatsApp account per company
        - WhatsApp message templates with variables
        - Template variables auto-fill from record data
        - Integration with Odoo contacts and partners
    ''',
    'author': 'SADEEM',
    'website': 'https://sadeem.cloud',
    'depends': ['base', 'mail', 'contacts'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/waha_session_views.xml',
        'views/waha_session_chat_views.xml',
        'views/res_company_views.xml',
        'views/whatsapp_message_views.xml',
        'views/whatsapp_template_views.xml',
        'views/whatsapp_template_variable_views.xml',
        'views/res_partner_views.xml',
        'wizard/send_whatsapp_wizard_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sadeem_waha_whatsapp/static/src/css/waha_style.css',
            'sadeem_waha_whatsapp/static/src/js/chatter_whatsapp_button.js',
            'sadeem_waha_whatsapp/static/src/xml/chatter_whatsapp_button.xml',
            'sadeem_waha_whatsapp/static/src/js/whatsapp_chat_dialog.js',
            'sadeem_waha_whatsapp/static/src/js/whatsapp_chat_button.js',
            'sadeem_waha_whatsapp/static/src/js/wizard_view_chat_button.js',
            'sadeem_waha_whatsapp/static/src/js/whatsapp_message_open_chat.js',
            'sadeem_waha_whatsapp/static/src/xml/whatsapp_chat_dialog.xml',
            'sadeem_waha_whatsapp/static/src/xml/whatsapp_chat_button.xml',
            'sadeem_waha_whatsapp/static/src/xml/wizard_view_chat_button.xml',
            # WhatsApp-Web-style live chat UI (merged from sadeem_waha_chat)
            'sadeem_waha_whatsapp/static/src/js/waha_chat_action.js',
            'sadeem_waha_whatsapp/static/src/js/waha_float.js',
            'sadeem_waha_whatsapp/static/src/xml/waha_chat_action.xml',
            'sadeem_waha_whatsapp/static/src/xml/waha_float.xml',
            'sadeem_waha_whatsapp/static/src/css/waha_chat.css',
        ],
    },
    'live_test_url': 'https://sadeem.cloud/request-demo',
    'installable': True,
    'auto_install': False,
    'application': True,
    'price': 75.00,
    'currency': 'USD',
    'images': ['static/description/banner.gif'],
    'license': 'OPL-1',
}