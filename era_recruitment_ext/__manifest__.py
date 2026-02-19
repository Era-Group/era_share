{
    'name': 'ERA Recruitment Extension',
    'summary': 'AI CV match analysis for applicants',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Recruitment',
    'author': 'ERA',
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
    'depends': [
        'attachment_indexation',
        'hr_recruitment',
        'ai',
    ],
    'data': [
        'data/ir_config_parameter.xml',
        'views/hr_applicant_views.xml',
        'views/res_config_settings_views.xml',
    ],
}
