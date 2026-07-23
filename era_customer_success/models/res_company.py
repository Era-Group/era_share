# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


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
    cs_ai_success_plan_enabled = fields.Boolean(
        string='AI Success Plan Drafts', default=False,
        help="When enabled, CSMs can ask AI to draft customer objectives, success "
             "criteria, stakeholders and milestones from the customer snapshot. "
             "Drafts require human review before activation.")
    cs_support_product_tmpl_ids = fields.Many2many(
        'product.template', 'res_company_cs_support_product_rel',
        'company_id', 'product_tmpl_id', string='Support Hours Products',
        help="Prepaid time products treated as customer support-hour wallets. "
             "Packages already linked to Helpdesk tickets are detected automatically.")
    cs_support_validity_days = fields.Integer(
        string='Support Package Validity (Days)', default=365)
    cs_support_low_threshold = fields.Float(
        string='Low Support Balance (%)', default=25.0)
    cs_support_critical_threshold = fields.Float(
        string='Critical Support Balance (%)', default=10.0)
    cs_support_expiry_warning_days = fields.Integer(
        string='Support Expiry Warning (Days)', default=30)

    @api.constrains(
        'cs_support_low_threshold', 'cs_support_critical_threshold',
        'cs_support_validity_days', 'cs_support_expiry_warning_days')
    def _check_support_wallet_settings(self):
        for company in self:
            if not 0 <= company.cs_support_critical_threshold <= company.cs_support_low_threshold <= 100:
                raise ValidationError(_(
                    'Support balance thresholds must be between 0 and 100, and the '
                    'critical threshold cannot exceed the low threshold.'))
            if company.cs_support_validity_days <= 0:
                raise ValidationError(_('Support package validity must be greater than zero days.'))
            if company.cs_support_expiry_warning_days < 0:
                raise ValidationError(_('Support expiry warning days cannot be negative.'))

    @api.constrains('cs_support_product_tmpl_ids')
    def _check_support_wallet_products(self):
        hour = self.env.ref('uom.product_uom_hour')
        for company in self:
            invalid = company.cs_support_product_tmpl_ids.filtered(
                lambda product: product.service_policy != 'ordered_prepaid'
                or not product.uom_id._has_common_reference(hour))
            if invalid:
                raise ValidationError(_(
                    'Support wallet products must be prepaid services measured in time: %s',
                    ', '.join(invalid.mapped('display_name'))))
