{
    'name': 'ERA: GOSI API Integration',
    'summary': 'Interact with GOSI Engagement Deduction API',
    'author': 'Era Group',
    'website': 'https://era.net.sa',
    'category': 'Human Resources',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
    'depends': [
        'base',
        'hr',
    ],
    'data': [
        'views/res_config_settings_views.xml',
        'views/hr_employee_views.xml',
        'data/hr_employee_gosi_actions.xml',
    ],
    'external_dependencies': {
        'python': ['jwt', 'cryptography'],
    },
}
