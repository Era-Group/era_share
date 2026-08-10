{
    'name': 'Chatter Cc',
    'summary': 'A Cc line in the chatter composer, delivered and shown as a '
               'real Cc',
    'description': """
Chatter Cc
==========
The backend chatter lets you pick who a message goes **To**, but there is no
way to simply copy somebody in.  People work around it by adding the person to
the To line, which is not the same thing, or by sending a second message.

This module adds a **Cc:** line under the existing **To:** line of the chatter
composer, for messages only - never for internal notes.

* The same autocomplete as the To line: existing contacts, "Create ...", and
  "Search More...".
* Everybody on Cc is notified through Odoo's own notification machinery, so
  they get their own e-mail in their own language, their own notification
  record, and the usual delivery-failure handling.
* Every notification e-mail carries a real, visible ``Cc:`` header, so
  recipients can see who was copied and "Reply All" reaches them.
* The Cc list is recorded on the message and shown in the recipients popover.
* Cc survives "Open full composer" and "Schedule message".
""",
    'version': '19.0.1.0.0',
    'category': 'Productivity/Discuss',
    'license': 'LGPL-3',
    'author': 'Era Group',
    'email': 'info@era.net.sa',
    'website': 'https://era.net.sa',
    'depends': ['mail'],
    'data': [
        'wizard/mail_compose_message_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_chatter_cc/static/src/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
