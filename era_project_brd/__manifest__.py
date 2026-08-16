{
    'name': "ERA Project BRD from Meetings",
    'summary': "Build an Odoo implementation BRD from project meeting transcripts",
    'version': "19.0.2.2.1",
    'category': "Services/Project",
    'author': "Era Group",
    'website': "https://era.net.sa",
    'license': "LGPL-3",
    'depends': [
        'era_sembly_meetings_tasks',
        'era_sembly_meetings_assemblyai',
        'era_ai_accounts',
        'documents_project',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/project_brd_security.xml',
        'data/ai_agent.xml',
        'data/ir_cron.xml',
        'views/project_project_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_project_brd/static/src/js/brd_progress_field.js',
            'era_project_brd/static/src/scss/brd.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
