{
    'name': "ERA Sembly.ai Meetings — Helpdesk Tickets",
    'summary': "Files Sembly.ai meetings on the right Helpdesk ticket — installs itself the moment Helpdesk is enabled",
    'description': """
The Helpdesk half of the Sembly meeting linking: a support call is filed exactly
where the support team looks, next to the opportunity, project and task links the
sibling modules make.

الشق الخاص بمكتب المساعدة في ربط اجتماعات Sembly: تُسجَّل مكالمة الدعم في المكان الذي
يبحث فيه فريق الدعم، إلى جانب ارتباطات الفرصة والمشروع والمهمة.

Why it is a separate module
---------------------------

The Helpdesk app is not installed on every database. Keeping the ticket link
here means the base app never depends on Helpdesk and stays installable
anywhere.

``auto_install`` means this bridge appears by itself once both the base app and
the Helpdesk app are installed, and stays uninstalled otherwise.

It contributes:

- ``ticket_id`` on ``sembly.meeting``, with its column, filter and group-by,
- ticket candidates to the AI matcher,
- the meetings smart button on the ticket form,
- the ticket as a recipient of the meeting-summary chatter note,
- a record rule that lets an agent see the meetings of their own tickets.
""",
    'author': "Era Group",
    'website': "https://era.net.sa",
    'category': "Productivity",
    'version': "19.0.1.0.0",
    'license': "LGPL-3",
    'depends': ['era_sembly_meetings', 'helpdesk'],
    'data': [
        'security/sembly_share_group.xml',
        'views/sembly_meeting_views.xml',
        'views/helpdesk_ticket_views.xml',
    ],
    'auto_install': True,
    'installable': True,
    'application': False,
}
