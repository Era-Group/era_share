# -*- coding: utf-8 -*-
{
    'name': 'Era Yusr API',
    'version': '19.0.2.16.0',
    'category': 'Human Resources',
    'summary': 'REST API backend for Yusr HR Mobile App (Employee ID + PIN login, JWT)',
    'description': """
Era Yusr API
============
Custom REST API backend for the Yusr employee self-service mobile app.

Features:
---------
* Employee ID + PIN authentication (no portal/internal user login)
* JWT token issuance and refresh
* PIN stored on the stock hr.employee.pin field (shared with the
  Attendance Kiosk); verified via hmac.compare_digest
* 20+ endpoints covering: profile, attendance, leaves, payslips,
  expenses, schedule, calendar, HR requests, push device registration
* Geofencing support for attendance check-in
* Full audit logging
    """,
    'author': 'Era Group',
    'website': 'https://era.net.sa',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'hr',
        'hr_attendance',
        'hr_holidays',
        'hr_expense',
        'mail',
        'calendar',
        'resource',
    ],
    'external_dependencies': {
        'python': ['PyJWT'],  # imported as `jwt`
    },
    'data': [
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        'views/hr_employee_views.xml',
        'views/hr_leave_type_views.xml',
        'views/res_config_settings_views.xml',
        'wizard/yusr_leave_type_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'post_init_hook': '_post_init_hook',
}
