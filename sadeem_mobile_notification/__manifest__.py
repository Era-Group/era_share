{
    'name': 'Sadeem Mobile Notification',
    'version': '19.0.1.0.0',
    'category': 'Technical',
    'summary': 'Push notification agent supporting ntfy and FCM',
    'description': """
Sadeem Mobile Notification
==========================

A standalone notification agent module that supports:

- ntfy (Self-hosted push notifications)
- Firebase Cloud Messaging (FCM)

Features:

- Device registration via REST API
- Rate limiting with monthly counters
- Notification logging and statistics
- Multi-company support
""",
    'author': 'Sadeem',
    'website': 'https://www.sadeem.cloud',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/notification_security.xml',
        'security/ir.model.access.csv',
        'data/notification_config_data.xml',
        'views/notification_config_views.xml',
        'views/notification_device_views.xml',
        'views/notification_log_views.xml',
        'views/res_company_views.xml',
        'views/menu_views.xml',
    ],
    'images': ['static/banner.gif'],
    'price': 40.00,
    'currency': 'EUR',
    'application': True,
    'installable': True,
    'auto_install': False,
    'pre_init_hook': 'pre_init_hook',
}
