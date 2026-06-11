# -*- coding: utf-8 -*-
{
    "name": "ERA CRM Lead AI Spam Guard",
    "version": "19.0.1.0.0",
    "category": "CRM",
    "summary": "Automatically tags spam leads using Odoo AI agent",
    "description": (
        "Creates an automated action on lead creation that asks an Odoo AI agent "
        "to classify the lead from its content and chatter, then adds the SPAM tag "
        "when the lead is not genuine."
    ),
    "author": "Era Group",
    "depends": ["crm", "ai", "base_automation"],
    "data": [
        "data/crm_tag.xml",
        "data/ai_agent.xml",
        "data/base_automation.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
