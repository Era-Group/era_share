from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    era_commission_journal_id = fields.Many2one(
        'account.journal', string='Commission Journal',
        help="Journal the settlement entry is posted in. A vendor bill uses the "
             "purchase journal instead.")
    era_commission_expense_account_id = fields.Many2one(
        'account.account', string='Commission Expense Account',
        help="Debited when a settlement is posted: the cost of the commission.")
    era_commission_payable_account_id = fields.Many2one(
        'account.account', string='Commission Payable Account',
        help="Credited when a settlement is posted: what the company owes the "
             "agent until it is paid.")
    era_commission_payout_mode = fields.Selection(
        selection=[
            ('entry', 'Journal Entry'),
            ('bill', 'Vendor Bill'),
        ],
        string='Default Payout', default='entry', required=True,
        help="Journal Entry suits an employee paid through the company. Vendor "
             "Bill suits an outside agent who invoices for the commission.")
    era_commission_default_plan_id = fields.Many2one(
        'era.commission.plan', string='Default Plan',
        help="Proposed when a new agent is created. Nothing is computed until "
             "the agent is actually assigned to a plan.")
    era_commission_tax_rate = fields.Float(
        string='Commission Tax Rate (%)', default=15.0, digits='Discount',
        help="Used when a plan takes the tax off the base by dividing: the net "
             "is the base divided by one plus this rate. Kept here rather than "
             "written into the code so a change of VAT does not need a patch.")
    era_commission_auto_generate = fields.Boolean(
        string='Daily Recomputation', default=True,
        help="Let the scheduled action recompute the current and previous month "
             "every night. Only draft lines are ever touched.")
