{
    'name': 'Era Nusuk Management',
    'version': '1.0',
    'category': 'Services',
    'summary': 'Manage Umrah visits, pilgrims, trips, visas, groups, agents, and hotels.',
    'description': """
Era Nusuk Management
====================

End-to-end management of Umrah operations, from Nusuk group arrival to
departure, in one Odoo application.

* Register pilgrims and track their visas, movements, and documents.
* Plan trips and manage groups, packages, and agent contracts.
* Book and follow up hotel accommodation in Makkah and Madinah.
* Schedule transport companies, vehicles, drivers, and route programs.
* Print operational reports: pilgrim cards, group manifests, hotel and
  transport reports.
""",
    'website': 'https://era.net.sa',
    'author': 'Era Group',
    'maintainer': 'Turki Marzoqi',
    'email': 'info@era.net.sa',
    'sequence': 1,
    'depends': ['base', 'mail', 'account', 'contacts', 'accountant'],
    'data': [
        'security/umrah_security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/nusuk_config_data.xml',
        'views/umrah_pilgrim_views.xml',
        'views/umrah_trip_views.xml',
        'views/umrah_hotel_views.xml',
        'views/umrah_visa_views.xml',
        'views/umrah_visa_inherit.xml',
        'views/umrah_group_views.xml',
        'views/umrah_group_inherit.xml',
        'views/umrah_agent_views.xml',
        'views/umrah_agent_inherit.xml',
        'views/umrah_customer_views.xml',
        'views/umrah_customer_contract_views.xml',
        'views/umrah_wizard_views.xml',

        'views/umrah_location_type_views.xml',
        'views/umrah_location_views.xml',
        'views/umrah_transport_driver_views.xml',
        'views/umrah_transport_vehicle_views.xml',
        'views/umrah_transport_company_views.xml',
        'views/umrah_transport_program_views.xml',
        'views/umrah_transport_program_inherit.xml',

        'reports/pilgrim_card_report.xml',

        'reports/pilgrim_report.xml',
        'reports/pilgrim_report_template.xml',

        'reports/group_report.xml',
        'reports/group_report_template.xml',

        'reports/agent_report.xml',
        'reports/customer_report.xml',
        'reports/contract_report.xml',
        'reports/hotel_report.xml',
'reports/hotel_booking_report.xml',
'reports/trip_report.xml',
'reports/transport_company_report.xml',
'reports/transport_program_report.xml',


        'views/umrah_menu.xml',
        'views/umrah_menu_hide.xml',
        'views/umrah_movement_views.xml',
        'views/umrah_package_views.xml',
        'views/umrah_masar_inherit_views.xml',
        'views/res_config_settings_views.xml',
        # 'reports/umrah_reports.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',

}
