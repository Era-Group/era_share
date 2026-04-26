{
    'name': 'Filestore Cleanup',
    'version': '15.0.1.0.0',
    'summary': 'Auto-cleanup system-generated report PDFs',
    'description': """
        Automatically deletes system-generated PDF reports for invoices,
        quotations, and checks older than 6 months. Runs every hour and
        deletes up to 100 files per run. Keeps user-uploaded and signed documents.
    """,
    'category': 'Technical',
    'author': 'Custom',
    'depends': ['account', 'sale'],
    'data': [
        'data/ir_cron.xml',
    ],
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
