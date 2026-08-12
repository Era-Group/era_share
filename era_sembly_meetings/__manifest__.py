{
    'name': "ERA Sembly.ai Meetings",
    'summary': "Every meeting, its transcript, summary and recording, in Odoo — the base app that the CRM, Tasks and Tickets linking modules build on",
    'description': """
Every meeting your team records in Sembly.ai lands in Odoo by itself — with its
transcript, its summary and its recording link — and is summarised in the chatter
of whichever record it turns out to be about.

كل اجتماع يسجله فريقك في Sembly.ai يصل إلى أودو تلقائيًا — بالتفريغ النصي والملخص
ورابط التسجيل — ويُكتب ملخصه في شاتر السجل الذي يخصه.

This is the BASE application
----------------------------

It owns the meeting itself: the two Sembly channels, the AI matching machinery,
the security model and the chatter posting. **It links a meeting to nothing on
its own** — it depends on neither CRM, nor Project, nor Helpdesk, so it installs
anywhere. Each link target is a separate module that plugs into the seams this
one defines:

- ``era_sembly_meetings_crm`` — the Opportunity link.
- ``era_sembly_meetings_tasks`` — the Project and Task links, plus the
  per-project "الاجتماعات" bucket task.
- ``era_sembly_meetings_tickets`` — the Helpdesk Ticket link.

Each installs itself the moment its own app is present, and can be uninstalled
without touching the others.

How it is put together
----------------------

Two complementary channels converge on ONE ``sembly.meeting`` record, keyed by the
Sembly meeting id, so the arrival order never matters:

- **MCP (primary, pull).** A dependency-free streamable-HTTP MCP client against
  ``https://mcp.sembly.ai/mcp`` provides ``list_meetings`` / ``get_meeting`` /
  ``list_tasks``. It gives metadata, minutes (summary) and the structured items
  (tasks, decisions, issues, risks, requirements, highlights), supports historical
  backfill and on-demand re-sync, and needs nothing but a token.
- **Custom Automation webhook (complement, push).** Sembly's MCP output schema
  carries NO transcript and NO recording link; its outbound automations do. The
  webhook fills in ``meeting_transcription``, ``meeting_link`` and participant
  emails as they arrive.

On top of that:

- A new "الاجتماعات" application with list / kanban / calendar / form views.
- The Odoo AI Agent proposes the link from a deterministically narrowed
  candidate set contributed by the installed link modules; a hand edit always
  wins and is never overwritten.
- The most recent meeting dated today or yesterday (company timezone) is posted
  once, as an INTERNAL note, on every record it is linked to.

Data minimisation: the transcript is never sent to the LLM — only title, date,
participant names and the summary.
""",
    'author': "Era Group",
    'website': "https://era.net.sa",
    'category': "Productivity",
    'version': "19.0.1.3.1",
    'license': "LGPL-3",
    'depends': ['base', 'mail', 'ai'],
    'data': [
        'security/sembly_groups.xml',
        'security/ir.model.access.csv',
        'security/sembly_record_rules.xml',
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'data/mail_templates.xml',
        'views/sembly_text_dialog_views.xml',
        'views/sembly_meeting_views.xml',
        'views/sembly_meeting_item_views.xml',
        'views/sembly_sync_log_views.xml',
        'views/sembly_import_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/sembly_menus.xml',
    ],
    'post_init_hook': '_sembly_post_init',
    'installable': True,
    'application': True,
}
