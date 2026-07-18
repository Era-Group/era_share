# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cs_ai_sentiment_enabled = fields.Boolean(
        related='company_id.cs_ai_sentiment_enabled', readonly=False,
        string='AI Sentiment Analysis')
    cs_ai_next_action_enabled = fields.Boolean(
        related='company_id.cs_ai_next_action_enabled', readonly=False,
        string='AI Next Best Action')
    cs_ai_summary_enabled = fields.Boolean(
        related='company_id.cs_ai_summary_enabled', readonly=False,
        string='AI Profile Summary')
    cs_ai_churn_enabled = fields.Boolean(
        related='company_id.cs_ai_churn_enabled', readonly=False,
        string='AI Churn Forecast')
    cs_ai_renewal_enabled = fields.Boolean(
        related='company_id.cs_ai_renewal_enabled', readonly=False,
        string='AI Renewal Strategy')
    cs_ai_digest_enabled = fields.Boolean(
        related='company_id.cs_ai_digest_enabled', readonly=False,
        string='AI Weekly Worklist')
