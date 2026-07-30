{
    "name": "CRM VoIP Call Insights",
    "summary": "Track VoIP calls, transcripts, and AI formatting in CRM.",
    "description": "Adds VoIP call counts to CRM leads and enhances call transcripts with AI formatting.",
    "version": "19.0.1.0.7",
    "author": "Era Group",
    "license": "LGPL-3",
    "depends": ["crm", "voip", "voip_ai", "voip_hr_recruitment", "era_ai_accounts"],
    "data": [
        "data/ai_agent.xml",
        "data/ir_cron.xml",
        "views/crm_lead_views.xml",
        "views/voip_call_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "era_voip_ext/static/src/js/recording_quality.js",
            "era_voip_ext/static/src/js/recording_player.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
}
