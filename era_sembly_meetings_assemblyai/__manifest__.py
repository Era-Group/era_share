{
    'name': "ERA Sembly.ai Meetings - AssemblyAI",
    'summary': "Fallback Arabic transcription for private Google Meet recordings",
    'description': """
Transcribes recent private Google Meet recordings through AssemblyAI when
Sembly has not delivered a transcript. The pipeline waits two hours for Sembly
when configured, starts immediately in Google-only mode, and never submits a
recording older than 48 hours.
    """,
    'author': "Era Group",
    'website': "https://era.net.sa",
    'category': "Productivity",
    'version': "19.0.1.0.0",
    'license': "LGPL-3",
    'depends': ['era_sembly_meetings_google', 'project'],
    'data': [
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
        'views/sembly_meeting_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
