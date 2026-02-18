# -*- coding: utf-8 -*-
{
    "name": "ERA Website Realtime Agent Widget & VoIP Call Recording with AI Summaries",
    "version": "19.0.1.0.0",
    "category": "Website",
    "summary": "Realtime voice assistant widget + VoIP call recording, transcription, and CRM summaries",
    "description": (
        "Adds a floating website widget to talk with an OpenAI Realtime agent, "
        "records calls, generates Arabic summaries, and stores transcripts/recordings "
        "in CRM. Integrates with Odoo VoIP to capture inbound/outbound calls in "
        "the browser, and can ingest SIP recordings via webhook."
    ),
    "depends": ["website", "web", "crm", "voip"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/widget.xml",
        "views/realtime_call_summary_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "era_voip_ai/static/src/js/realtime_agent_widget.js",
            "era_voip_ai/static/src/scss/realtime_agent_widget.scss",
        ],
        "web.assets_backend": [
            "era_voip_ai/static/src/js/voip_call_recording.js",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
