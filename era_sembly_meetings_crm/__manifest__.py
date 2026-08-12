{
    'name': "ERA Sembly.ai Meetings — CRM",
    'summary': "Files Sembly.ai meetings on the right opportunity — installs itself the moment CRM is enabled",
    'description': """
The CRM half of the Sembly meeting linking: a sales call is filed on the
opportunity it was about, and its summary lands in that opportunity's chatter.

الشق الخاص بإدارة علاقات العملاء في ربط اجتماعات Sembly: تُسجَّل المكالمة على الفرصة
التي تخصها، ويصل ملخصها إلى شاتر تلك الفرصة.

Why it is a separate module
---------------------------

``era_sembly_meetings`` owns the meeting, the two Sembly channels and the AI
machinery, but links a meeting to nothing by itself. Keeping the opportunity
link here means the base app never depends on CRM and stays installable on a
database that does not sell anything.

``auto_install`` means this bridge appears by itself once both the base app and
the CRM app are installed, and stays uninstalled otherwise.

It contributes:

- ``lead_id`` on ``sembly.meeting``, and its column, filter and group-by,
- opportunity candidates to the AI matcher, narrowed by participant and by
  meeting-title token exactly as the other link modules narrow theirs,
- the meetings smart button on the opportunity form,
- the opportunity as a recipient of the meeting-summary chatter note,
- a record rule that lets a salesperson see the meetings of their own
  opportunities.
""",
    'author': "Era Group",
    'website': "https://era.net.sa",
    'category': "Productivity",
    'version': "19.0.1.0.0",
    'license': "LGPL-3",
    'depends': ['era_sembly_meetings', 'crm'],
    'data': [
        'security/sembly_share_group.xml',
        'views/sembly_meeting_views.xml',
        'views/crm_lead_views.xml',
    ],
    'auto_install': True,
    'installable': True,
    'application': False,
}
