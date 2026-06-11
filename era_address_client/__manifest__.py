{
    'name': 'ERA Address Client',
    'summary': 'Client for ERA Address Lookup service via service.era.net.sa',
    'version': '19.0.1.0.0',
    'author': 'Era Group',
    'website': 'https://era.net.sa/',
    'category': 'Contacts',
    'depends': ['base', 'contacts'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        # 'views/res_config_settings_views.xml',  # hidden for now — re-enable to expose the settings block
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
    'external_dependencies': {
        'python': ['requests'],
    },
}
