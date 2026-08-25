from odoo import fields, models
class ResCompany(models.Model):
    _inherit='res.company'
    legal_default_city=fields.Char(); legal_hearing_reminder_days=fields.Integer(default=3)
    legal_trust_liability_account_id=fields.Many2one('account.account',check_company=True)
    legal_trust_bank_account_id=fields.Many2one('account.account',check_company=True)
    legal_trust_receivable_account_id=fields.Many2one('account.account',check_company=True)
    legal_trust_journal_id=fields.Many2one('account.journal',check_company=True)
class ResConfigSettings(models.TransientModel):
    _inherit='res.config.settings'
    legal_default_city=fields.Char(related='company_id.legal_default_city',readonly=False)
    legal_hearing_reminder_days=fields.Integer(related='company_id.legal_hearing_reminder_days',readonly=False)
    legal_trust_liability_account_id=fields.Many2one(related='company_id.legal_trust_liability_account_id',readonly=False)
    legal_trust_bank_account_id=fields.Many2one(related='company_id.legal_trust_bank_account_id',readonly=False)
    legal_trust_receivable_account_id=fields.Many2one(related='company_id.legal_trust_receivable_account_id',readonly=False)
    legal_trust_journal_id=fields.Many2one(related='company_id.legal_trust_journal_id',readonly=False)

