from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.era_sales_commission.models.commission_agent_rate import (
    COMMISSION_TYPES,
)

#: Which plan bases feed which commission type, for the plan domain.
BASES_PER_TYPE = {
    'sales': ('order', 'sales', 'margin'),
    'collection': ('collection',),
    'qty_sold': ('qty_sold',),
    'qty_collected': ('qty_collected',),
}


class EraCommissionGenerate(models.TransientModel):
    """The monthly run: recompute the period, then open a settlement per agent."""

    _name = 'era.commission.generate'
    _description = 'Generate Commission Settlements'

    date_from = fields.Date(
        string='From', required=True,
        default=lambda self: (
            fields.Date.context_today(self) - relativedelta(months=1)).replace(day=1))
    date_to = fields.Date(
        string='To', required=True,
        default=lambda self: fields.Date.end_of(
            (fields.Date.context_today(self) - relativedelta(months=1)), 'month'))
    commission_type = fields.Selection(
        selection=COMMISSION_TYPES, string='Commission Type',
        help="Leave empty to run every kind of commission at once. Set it to "
             "settle one kind on its own -- the sales commission of the month "
             "and the collection commission of the month are two different "
             "conversations with a rep.")
    plan_domain = fields.Binary(
        string='Plan Domain', compute='_compute_plan_domain', exportable=False)
    plan_ids = fields.Many2many(
        'era.commission.plan', string='Plans',
        help="Leave empty to run every approved plan.")
    deduct_tax = fields.Boolean(
        string='Deduct Tax', default=True,
        help="Overrides what the plans say for this run.")
    tax_method = fields.Selection(
        selection=[
            ('actual', 'Actual Tax of the Document'),
            ('divide', 'Divide by the Tax Rate'),
        ],
        string='Tax Method', default='actual', required=True)
    use_target = fields.Boolean(
        string='Deduct Target', default=True,
        help="Take the agent's target of the period off the base, when the "
             "plan is set to deduct it. Untick to run a period without it.")
    agent_ids = fields.Many2many(
        'era.commission.agent', string='Agents',
        help="Leave empty to settle every agent with something to be paid.")
    reuse_draft = fields.Boolean(
        string='Reuse Draft Settlements', default=True,
        help="Add the lines to a draft settlement of the same agent and period "
             "when one exists, instead of opening a second one.")
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wizard in self:
            if wizard.date_to < wizard.date_from:
                raise UserError(wizard.env._(
                    "The end date is before the start date."))

    @api.depends('commission_type')
    def _compute_plan_domain(self):
        for wizard in self:
            domain = [('state', '=', 'approved')]
            if wizard.commission_type:
                domain.append(
                    ('basis', 'in', list(BASES_PER_TYPE[wizard.commission_type])))
            wizard.plan_domain = domain

    @api.onchange('commission_type')
    def _onchange_commission_type(self):
        """Drop plans that no longer answer the chosen kind of commission."""
        if self.commission_type and self.plan_ids:
            bases = BASES_PER_TYPE[self.commission_type]
            self.plan_ids = self.plan_ids.filtered(
                lambda plan: plan.basis in bases)

    def action_generate(self):
        self.ensure_one()
        Engine = self.env['era.commission.engine'].with_company(self.company_id)
        Line = self.env['era.commission.line']
        Settlement = self.env['era.commission.settlement']

        plans = self.plan_ids
        if not plans and self.commission_type:
            plans = self.env['era.commission.plan'].search([
                ('state', '=', 'approved'),
                ('company_id', '=', self.company_id.id),
                ('basis', 'in', list(BASES_PER_TYPE[self.commission_type])),
            ])

        Engine.generate(
            self.date_from, self.date_to,
            plans=plans or None,
            agents=self.agent_ids or None,
            options={
                'deduct_tax': self.deduct_tax,
                'tax_method': self.tax_method,
                'use_target': self.use_target,
            })

        domain = [
            ('company_id', '=', self.company_id.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'draft'),
            ('settlement_id', '=', False),
        ]
        if self.commission_type:
            # the manager's override is an adjustment, and it belongs on the
            # same statement as the lines it was derived from
            domain.append(('commission_type', 'in',
                           (self.commission_type, 'adjustment')))
        if self.plan_ids:
            domain.append(('plan_id', 'in', self.plan_ids.ids))
        if self.agent_ids:
            domain.append(('agent_id', 'in', self.agent_ids.ids))

        settlements = Settlement.browse()
        for agent, in Line._read_group(domain, ['agent_id'], []):
            settlement = Settlement.browse()
            if self.reuse_draft:
                settlement = Settlement.search([
                    ('agent_id', '=', agent.id),
                    ('date_from', '=', self.date_from),
                    ('date_to', '=', self.date_to),
                    ('state', '=', 'draft'),
                    ('company_id', '=', self.company_id.id),
                ], limit=1)
            if not settlement:
                settlement = Settlement.create({
                    'agent_id': agent.id,
                    'date_from': self.date_from,
                    'date_to': self.date_to,
                    'plan_ids': [(6, 0, self.plan_ids.ids)],
                    'payout_mode': agent.payout_mode,
                    'company_id': self.company_id.id,
                })
            lines = Line.search(domain + [('agent_id', '=', agent.id)])
            lines.write({'settlement_id': settlement.id, 'state': 'confirmed'})
            settlement.amount_target_factor = \
                settlement._compute_target_factor() * 100.0
            settlements |= settlement

        if not settlements:
            raise UserError(self.env._(
                "Nothing to settle over this period. Check that the plans are "
                "approved and that the agents are assigned to them."))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Commission Settlements'),
            'res_model': 'era.commission.settlement',
            'view_mode': 'list,form',
            'domain': [('id', 'in', settlements.ids)],
        }
