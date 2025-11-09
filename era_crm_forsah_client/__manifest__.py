# -*- coding: utf-8 -*-
{
    'name': "Era Crm Forsah & Etimad For Clients",
    'version': '19.0.1.0.0',
    'category': 'CRM',
    'summary': 'Manage Forsah & Etimad Clients',
    'description': """
       Era Crm Forsah & Etimad For Clients 
    """,
    'author': 'Era group',
    'email': 'aqlan@era.net.sa',
    'website': 'https://era.net.sa',
    'license': 'AGPL-3',
    'depends': ['base', 'contacts', 'crm', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/crm_forsah_view.xml',
        'views/crm_etimad_view.xml',
        'data/cronjob.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_crm_forsah_client/static/src/css/styles.css',
        ],
    },
    'installable': True,
    'application': True,
}

