from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_commission_journal_id = fields.Many2one(
        related='company_id.era_commission_journal_id', readonly=False)
    era_commission_expense_account_id = fields.Many2one(
        related='company_id.era_commission_expense_account_id', readonly=False)
    era_commission_payable_account_id = fields.Many2one(
        related='company_id.era_commission_payable_account_id', readonly=False)
    era_commission_payout_mode = fields.Selection(
        related='company_id.era_commission_payout_mode', readonly=False)
    era_commission_default_plan_id = fields.Many2one(
        related='company_id.era_commission_default_plan_id', readonly=False)
    era_commission_tax_rate = fields.Float(
        related='company_id.era_commission_tax_rate', readonly=False)
    era_commission_auto_generate = fields.Boolean(
        related='company_id.era_commission_auto_generate', readonly=False)
