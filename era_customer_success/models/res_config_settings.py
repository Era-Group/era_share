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
    cs_ai_success_plan_enabled = fields.Boolean(
        related='company_id.cs_ai_success_plan_enabled', readonly=False,
        string='AI Success Plan Drafts')
    cs_ai_value_review_enabled = fields.Boolean(
        related='company_id.cs_ai_value_review_enabled', readonly=False,
        string='AI Value Review Drafts')
    cs_support_product_tmpl_ids = fields.Many2many(
        related='company_id.cs_support_product_tmpl_ids', readonly=False,
        string='Support Hours Products')
    cs_support_validity_days = fields.Integer(
        related='company_id.cs_support_validity_days', readonly=False,
        string='Support Package Validity (Days)')
    cs_support_low_threshold = fields.Float(
        related='company_id.cs_support_low_threshold', readonly=False,
        string='Low Support Balance (%)')
    cs_support_critical_threshold = fields.Float(
        related='company_id.cs_support_critical_threshold', readonly=False,
        string='Critical Support Balance (%)')
    cs_support_expiry_warning_days = fields.Integer(
        related='company_id.cs_support_expiry_warning_days', readonly=False,
        string='Support Expiry Warning (Days)')
