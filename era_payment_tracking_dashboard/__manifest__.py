# -*- coding: utf-8 -*-
{
    'name': 'متابعة الدفعات',
    'summary': 'نظام متكامل لمتابعة الدفعات المستحقة من العملاء',
    'description': """
متابعة الدفعات من العملاء
==========================
نظام متكامل لمتابعة الدفعات المستحقة بناءً على أوامر البيع المؤكدة
وسياسات الدفع، مع تذكيرات عبر واتساب والبريد الإلكتروني، ولوحات متابعة
(Kanban / List / Pivot / Graph) وأقساط محسوبة تلقائيًا.
""",
    'version': '19.0.1.0.0',
    'author': 'Era Group',
    'license': 'LGPL-3',
    'category': 'Sales/Accounting',
    'depends': [
        'base',
        'sale',
        'account',
        'mail',
        'web',
        'base_automation',
    ],
    'data': [
        'security/payment_tracking_security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/email_templates.xml',
        'data/automated_actions.xml',
        'views/payment_tracking_views.xml',
        'views/menu.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
}
