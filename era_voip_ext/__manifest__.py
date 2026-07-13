{
    "name": "CRM VoIP Call Insights",
    "summary": "Track VoIP calls, transcripts, and AI formatting in CRM.",
    "description": "Adds VoIP call counts to CRM leads and enhances call transcripts with AI formatting.",
    "version": "19.0.1.0.3",
    "author": "Era Group",
    "license": "LGPL-3",
    "depends": ["crm", "voip", "voip_ai", "voip_hr_recruitment"],
    "data": [
        "data/ai_agent.xml",
        "data/ir_cron.xml",
        "views/crm_lead_views.xml",
        "views/voip_call_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "era_voip_ext/static/src/scss/voip_call.scss",
            "era_voip_ext/static/src/js/recording_quality.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
}
