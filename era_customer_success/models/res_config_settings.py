# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError


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
    cs_ai_adoption_enabled = fields.Boolean(
        related='company_id.cs_ai_adoption_enabled', readonly=False,
        string='AI Adoption Plans')
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
    cs_google_sheet_enabled = fields.Boolean(config_parameter='era_customer_success.google_sheet_enabled', string='Automatic Google Sheet Sync')
    cs_google_spreadsheet_id = fields.Char(config_parameter='era_customer_success.google_spreadsheet_id', string='Spreadsheet ID')
    cs_google_sheet_gid = fields.Integer(config_parameter='era_customer_success.google_sheet_gid', string='Sheet Tab GID', default=1481647876)
    cs_google_service_account_json = fields.Char(config_parameter='era_customer_success.google_service_account_json', string='Service Account JSON')
    cs_google_sharing_approved = fields.Boolean(
        config_parameter='era_customer_success.google_sharing_approved',
        string='Google Sheet Information Sharing Approved')
    cs_google_approval_scope = fields.Char(
        config_parameter='era_customer_success.google_approval_scope',
        string='Detected Red-Title Scope', readonly=True)
    cs_google_approved_by = fields.Char(
        config_parameter='era_customer_success.google_approved_by',
        string='Approved By', readonly=True)
    cs_google_approved_on = fields.Datetime(
        config_parameter='era_customer_success.google_approved_on',
        string='Approved On', readonly=True)

    def action_scan_google_sheet_scope(self):
        self.ensure_one()
        self.set_values()
        sync = self.env['cs.google.sheet.sync']
        settings = sync._settings()
        if not settings['spreadsheet_id']:
            raise UserError(_('أدخل معرف Google Sheet أولاً.'))
        if not settings['credentials']:
            raise UserError(_('لفحص عناوين Sheet الخاصة، أدخل بيانات حساب خدمة Google أولاً.'))
        token = sync._access_token(settings['credentials'])
        red_columns = sync._red_header_columns(settings['spreadsheet_id'], settings['gid'], token)
        scope = ', '.join(sorted(red_columns, key=lambda column: ord(column)))
        self.cs_google_approval_scope = scope
        self.cs_google_sharing_approved = False
        self.cs_google_approved_by = False
        self.cs_google_approved_on = False
        self.set_values()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_approve_google_sharing(self):
        self.ensure_one()
        if not (self.cs_google_approval_scope or '').strip():
            raise UserError(_('Scan the Google Sheet scope before approving synchronization.'))
        self.cs_google_sharing_approved = True
        self.cs_google_approved_by = self.env.user.display_name
        self.cs_google_approved_on = fields.Datetime.now()
        self.set_values()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_revoke_google_sharing(self):
        self.ensure_one()
        self.cs_google_sharing_approved = False
        self.cs_google_approved_by = False
        self.cs_google_approved_on = False
        self.set_values()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_sync_google_sheet(self):
        self.ensure_one()
        return self.env['cs.google.sheet.sync'].action_sync()
