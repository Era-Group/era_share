# Part of Era Group custom addons.
{
    'name': 'Era WAHA WhatsApp Integration',
    'version': '19.0.1.19.2',
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

سياسة تنبيهات WAHA
------------------
تنبه الرسائل الواردة الجديدة المستخدمين الافتراضيين للحساب إلى أن يرد موظف
داخلي، أو يكتب ملاحظة داخلية، أو تتم الإشارة إليه في ملاحظة داخلية. بعدها تصل
الرسائل الواردة إلى المشاركين في المحادثة فقط. لا تنشئ الرسائل الصادرة أو
السجل المستورد تنبيهات. وتُصعّد الرسالة الواردة الحية غير المُجاب عنها مرة
واحدة إلى المستخدمين الافتراضيين بعد 60 دقيقة خلال أوقات دوام الشركة.
""",
    'author': 'Era Group',
    'website': 'https://era.net.sa',
    'license': 'LGPL-3',
    'depends': ['whatsapp'],
    'data': [
        'security/ir.model.access.csv',
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
    'application': True,
}
