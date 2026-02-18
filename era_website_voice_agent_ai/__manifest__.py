# -*- coding: utf-8 -*-
{
    "name": "ERA Website Realtime Agent Widget with AI Summaries",
    "version": "19.0.1.0.0",
    "author": "Era Group",
    "category": "Website",
    "summary": "Realtime voice assistant widget with recording, transcription, and CRM summaries",
    "description": (
        "Adds a floating website widget to talk with an OpenAI Realtime agent, "
        "records calls, generates Arabic summaries, and stores transcripts/recordings "
        "in CRM. Recording and summarization are scoped to the Realtime Agent Widget only."
    ),
    "depends": ["website", "web", "crm"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/widget.xml",
        "views/realtime_call_summary_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "era_website_voice_agent_ai/static/src/js/realtime_agent_widget.js",
            "era_website_voice_agent_ai/static/src/scss/realtime_agent_widget.scss",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
