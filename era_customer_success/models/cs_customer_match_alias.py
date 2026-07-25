import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError


def customer_alias_key(value):
    text = (value or '').casefold().translate(str.maketrans({
        'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا', 'ؤ': 'و', 'ئ': 'ي',
        'ى': 'ي', 'ة': 'ه', 'ـ': '',
    }))
    text = re.sub(r'[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]', '', text)
    return re.sub(r'[^\w]+', ' ', text).strip()


class CsCustomerMatchAlias(models.Model):
    _name = 'cs.customer.match.alias'
    _description = 'Customer Matching Alias'
    _order = 'state, alias_name'
    _check_company_auto = True

    alias_name = fields.Char(required=True, string='Excel Customer Name')
    alias_key = fields.Char(required=True, index=True, readonly=True)
    account_id = fields.Many2one(
        'cs.account', string='Matched Customer Success Record', check_company=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    state = fields.Selection([
        ('pending', 'Needs Review'), ('approved', 'Approved'),
    ], required=True, default='pending')
    source = fields.Selection([
        ('automatic', 'Automatic'), ('manual', 'Manual'),
    ], required=True, default='automatic', readonly=True)
    confidence = fields.Float(readonly=True)
    reason = fields.Char(readonly=True)
    last_seen_on = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True)

    _alias_company_unique = models.Constraint(
        'unique(company_id, alias_key)',
        'This Excel customer alias already exists for the company.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['alias_key'] = customer_alias_key(vals.get('alias_name'))
        return super().create(vals_list)

    def write(self, vals):
        if 'alias_name' in vals:
            vals = dict(vals, alias_key=customer_alias_key(vals['alias_name']))
        return super().write(vals)

    def action_approve(self):
        for alias in self:
            if not alias.account_id:
                raise UserError(_('Select a Customer Success record before approving the alias.'))
        self.write({'state': 'approved', 'source': 'manual'})

    def action_reset_to_review(self):
        self.write({'state': 'pending'})
