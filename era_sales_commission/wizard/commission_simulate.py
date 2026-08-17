from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.era_sales_commission.models.commission_agent_rate import (
    LINE_COMMISSION_TYPES,
)


class EraCommissionSimulate(models.TransientModel):
    """Answer "what would I earn" without writing a single commission line.

    It runs the very same engine the real computation uses, with ``commit``
    off, so what it shows is what would be created -- not a second, slightly
    different formula living in a wizard.
    """

    _name = 'era.commission.simulate'
    _description = 'Simulate Commission'

    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent', required=True)
    date_from = fields.Date(
        string='From', required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(
        string='To', required=True,
        default=lambda self: fields.Date.end_of(
            fields.Date.context_today(self), 'month'))
    plan_ids = fields.Many2many(
        'era.commission.plan', string='Plans',
        domain="[('state', '=', 'approved')]",
        help="Leave empty to run every plan the agent is assigned to.")
    result_ids = fields.One2many(
        'era.commission.simulate.line', 'wizard_id', string='Result', readonly=True)
    amount_total = fields.Monetary(
        string='Expected Commission', compute='_compute_amount_total',
        currency_field='currency_id')
    base_total = fields.Monetary(
        string='Base', compute='_compute_amount_total',
        currency_field='currency_id')
    simulated = fields.Boolean(string='Simulated', default=False)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    @api.depends('result_ids.commission_amount', 'result_ids.base_amount')
    def _compute_amount_total(self):
        for wizard in self:
            wizard.amount_total = sum(
                wizard.result_ids.mapped('commission_amount'))
            wizard.base_total = sum(wizard.result_ids.mapped('base_amount'))

    def action_simulate(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(self.env._("The end date is before the start date."))
        plans = self.plan_ids or self.agent_id._get_assignments(self.date_to).plan_id
        vals_list = self.env['era.commission.engine'].with_company(
            self.company_id
        ).generate(
            self.date_from, self.date_to, plans=plans, agents=self.agent_id,
            commit=False)
        self.result_ids.unlink()
        # the amount is read off an unsaved commission line, so the simulation
        # and the real run cannot drift apart: it is the same formula, once.
        Line = self.env['era.commission.line']
        self.env['era.commission.simulate.line'].create([{
            'wizard_id': self.id,
            'date': vals['date'],
            'name': vals['name'],
            'plan_id': vals['plan_id'],
            'rule_id': vals['rule_id'],
            'commission_type': vals['commission_type'],
            'partner_id': vals['partner_id'],
            'product_id': vals['product_id'],
            'quantity': vals['quantity'],
            'base_amount': vals['base_amount'],
            'rate': vals['rate'],
            'unit_price': vals['unit_price'],
            'commission_amount': Line.new(vals).commission_amount,
        } for vals in vals_list])
        self.simulated = True
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }


class EraCommissionSimulateLine(models.TransientModel):
    _name = 'era.commission.simulate.line'
    _description = 'Simulated Commission Line'
    _order = 'date, id'

    wizard_id = fields.Many2one(
        'era.commission.simulate', string='Simulation', required=True,
        ondelete='cascade', index=True)
    date = fields.Date(string='Date')
    name = fields.Char(string='Description')
    plan_id = fields.Many2one('era.commission.plan', string='Plan')
    rule_id = fields.Many2one('era.commission.rule', string='Rule')
    partner_id = fields.Many2one('res.partner', string='Customer')
    product_id = fields.Many2one('product.product', string='Product')
    commission_type = fields.Selection(
        selection=LINE_COMMISSION_TYPES, string='Commission Type')
    quantity = fields.Float(string='Quantity', digits='Product Unit')
    base_amount = fields.Monetary(string='Base', currency_field='currency_id')
    rate = fields.Float(string='Rate (%)', digits='Discount')
    unit_price = fields.Monetary(
        string='Unit Price', currency_field='currency_id')
    commission_amount = fields.Monetary(
        string='Commission', currency_field='currency_id')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
