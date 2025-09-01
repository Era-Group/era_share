# -*- coding: utf-8 -*-
{
    'currency': 'USD',
    'price': 799,
    'license': 'AGPL-3',
    'name': "Era Muqeem ",

    'summary': """
            تكامل مع منصة مُقيم - Muqeem integration API (Annual subscription) 
    إصدار وتجديد ونقل إقامة - إصدار وإلغاء وتمديد تأشيرة الخروج والعودة والنهائي - تقرير مقيم - تقارير بالعمليات. وغيرها

        """,

    'description': """

        تكامل مع منصة مُقيم - Muqeem integration API (Annual subscription) 
    إصدار وتجديد ونقل إقامة - إصدار وإلغاء وتمديد تأشيرة الخروج والعودة والنهائي - تقرير مقيم - تقارير بالعمليات. وغيرها
        """,

    "author": "Era group",
    "email": "aqlan@era.net.sa",
    "website": "https://era.net.sa",
    'category': 'Hr',
    'version': '17.0.1.0.0',
    'images': ['static/description/muqeem_services.png'],

    # any module necessary for this one to work correctly
    'depends': ['base', 'hr'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'security/muqeem_security.xml',
        'views/views.xml',
        'views/templates.xml',
        'views/menus2.xml',
        'views/isuue_exit_entry.xml',
        'views/extend_exit_entry.xml',
        'views/cancell_exit_entry.xml',
        'views/reprint_exit_entry.xml',
        'views/final_exit.xml',
        'views/cancel_final_exit.xml',
        'views/renew_iqama.xml',
        'views/transfer_iqama.xml',
        'views/extend_passport_validaty.xml',
        'views/print_muqeem_report.xml',
        'views/today_requests_report.xml',
        'views/print_muqeem_confirm.xml',
        'views/hr_employee.xml',
        'views/issue_exit_entry_template.xml',
        'views/extend_exit_entry_template.xml',
        'views/cancell_exit_entry_template.xml',
        'views/reprint_exit_entry_template.xml',
        'views/final_exit_template.xml',
        'views/renew_iqama_template.xml',
        'views/transfer_iqama_template.xml',
        'views/extend_passport_validaty_template.xml',
        'views/print_muqeem_report_template.xml',
        'views/today_requests_report_template.xml',
        'views/client_requirement_views.xml',
        'views/res_company.xml',

    ],
    'demo': [
        'demo/demo.xml',
    ],
    "application": True,
}

