{
    'name': 'ERA Employee Reassign Activities',
    'summary': 'Reassign future activities, leads, meetings, tickets, tasks and orders from one user to another',
    'description': """
When an employee leaves the company, their pending work shouldn't be lost.
This module adds a "Reassign Work" action on the user form to transfer:
  - Future scheduled activities (mail.activity)
  - Open CRM leads/opportunities
  - Future calendar events (as organizer)
  - Open helpdesk tickets (if Helpdesk is installed)
  - Open project tasks (if Project is installed)
  - Draft/Sent sales orders (if Sales is installed)
to another user in a single click.
    """,
    'version': '19.0.1.0.0',
    'category': 'Human Resources',
    'author': 'ERA',
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
    'depends': [
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'wizard/era_emp_reassign_wizard_views.xml',
        'views/res_users_views.xml',
    ],
}
