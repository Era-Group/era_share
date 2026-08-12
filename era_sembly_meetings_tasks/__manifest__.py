{
    'name': "ERA Sembly.ai Meetings — Projects & Tasks",
    'summary': "Files Sembly.ai meetings on the right project or task — installs itself the moment Project is enabled",
    'description': """
The delivery half of the Sembly meeting linking: an implementation call is filed
on the task it was about, or — when the meeting is about the project at large —
under a per-project "الاجتماعات" bucket task, created once and reused.

الشق الخاص بالمشاريع والمهام في ربط اجتماعات Sembly: تُسجَّل مكالمة التنفيذ على المهمة
التي تخصها، أو تحت تاسك "الاجتماعات" الخاص بالمشروع عندما يكون الاجتماع عن المشروع ككل.

Why it is a separate module
---------------------------

``era_sembly_meetings`` owns the meeting, the two Sembly channels and the AI
machinery, but links a meeting to nothing by itself. Keeping the project and
task links here means the base app never depends on Project.

``auto_install`` means this bridge appears by itself once both the base app and
the Project app are installed, and stays uninstalled otherwise.

It contributes:

- ``project_id`` and ``task_id`` on ``sembly.meeting``, with their columns,
  filters and group-bys,
- project and task candidates to the AI matcher,
- the rule that a chosen task implies its project, so the model returning only
  a task still produces a complete link,
- the per-project "الاجتماعات" bucket task, and its two settings,
- the meetings smart button on both the project and the task form — the
  project's count folds in the meetings sitting on its tasks,
- the task as a recipient of the meeting-summary chatter note,
- a record rule that lets a project manager and a task assignee see their own
  meetings.
""",
    'author': "Era Group",
    'website': "https://era.net.sa",
    'category': "Productivity",
    'version': "19.0.1.0.0",
    'license': "LGPL-3",
    'depends': ['era_sembly_meetings', 'project'],
    'data': [
        'security/sembly_share_group.xml',
        'views/sembly_meeting_views.xml',
        'views/project_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': '_sembly_tasks_post_init',
    'auto_install': True,
    'installable': True,
    'application': False,
}
