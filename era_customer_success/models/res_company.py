# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    cs_ai_sentiment_enabled = fields.Boolean(
        string='AI Sentiment Analysis', default=False,
        help="When enabled, customer support tickets are analysed by the AI agent to "
             "score sentiment, which feeds the customer health score and triggers "
             "service-recovery alerts on negative sentiment. NOTE: ticket text is sent "
             "to the configured AI provider.")
    cs_ai_next_action_enabled = fields.Boolean(
        string='AI Next Best Action', default=False,
        help="When enabled, a daily job asks the AI agent to recommend the next best "
             "action for each customer based on their situation and history. NOTE: the "
             "customer snapshot is sent to the configured AI provider.")
    cs_ai_summary_enabled = fields.Boolean(
        string='AI Profile Summary', default=False,
        help="When enabled, a scheduled job gradually generates a short AI profile "
             "summary (situation, scope, field of work, tenure) shown under each "
             "customer's name. NOTE: the customer snapshot is sent to the AI provider.")
    cs_ai_churn_enabled = fields.Boolean(
        string='AI Churn Forecast', default=False,
        help="When enabled, a daily job predicts each customer's churn probability at "
             "30/60/90 days (with the top drivers) from its situation and KPI history, "
             "for all active customers. NOTE: snapshots are sent to the AI provider.")
    cs_ai_renewal_enabled = fields.Boolean(
        string='AI Renewal Strategy', default=False,
        help="When enabled, a daily job builds a renewal play (which lever to pull plus "
             "dated steps) for every customer inside the 90-day renewal window. "
             "NOTE: the customer snapshot is sent to the AI provider.")
    cs_ai_digest_enabled = fields.Boolean(
        string='AI Weekly Worklist', default=False,
        help="When enabled, a weekly job delivers each CSM a ranked worklist of the "
             "accounts in their portfolio that need attention this week. "
             "NOTE: portfolio signals are sent to the AI provider.")
