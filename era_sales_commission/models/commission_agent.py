from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EraCommissionAgent(models.Model):
    """Whoever earns a commission.

    A separate model, not ``res.users``: an outside rep who never logs in still
    has to be paid, a manager is paid on what the people under them sell, and
    the money leaves either as a journal entry or as a vendor bill on a partner.
    None of that fits on a user account.
    """

    _name = 'era.commission.agent'
    _description = 'Commission Agent'
    _inherit = ['mail.thread']
    _order = 'name'
    _parent_store = True

    name = fields.Char(string='Name', required=True, tracking=True)
    code = fields.Char(string='Reference', copy=False, readonly=True, default='/')
    active = fields.Boolean(string='Active', default=True)
    user_id = fields.Many2one(
        'res.users', string='Related User', tracking=True,
        help="The salesperson this agent is on a sales order or an invoice. Used "
             "to attach a document to an agent when nothing else says who sold it.")
    employee_id = fields.Many2one(
        'hr.employee', string='Employee',
        help="Needed to push a settlement to a payslip. Nothing is ever pushed "
             "on its own: it is a button a human presses.")
    partner_id = fields.Many2one(
        'res.partner', string='Payment Partner',
        help="Who the money is owed to. Required when the settlement is paid out "
             "as a vendor bill.")
    team_id = fields.Many2one('crm.team', string='Sales Team')
    parent_id = fields.Many2one(
        'era.commission.agent', string='Manager', ondelete='restrict', index=True,
        help="The agent who earns an override on what this one sells.")
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many(
        'era.commission.agent', 'parent_id', string='Team Members')
    override_rate = fields.Float(
        string='Override Rate (%)', digits='Discount',
        help="What this agent earns on the sales of the agents under them.")
    override_basis = fields.Selection(
        selection=[
            ('commission', 'Share of their commission'),
            ('base', 'Percentage of their base'),
        ],
        string='Override Basis', default='commission', required=True,
        help="Share of their commission: the rate applies to what the team member "
             "earned. Percentage of their base: it applies to the amount the team "
             "member's commission was computed on.")
    assignment_ids = fields.One2many(
        'era.commission.agent.plan', 'agent_id', string='Commission Plans')
    rate_ids = fields.One2many(
        'era.commission.agent.rate', 'agent_id', string='Rates and Unit Prices',
        help="What this agent earns per commission type. The percentage set "
             "here wins over the percentage of a plan rule.")
    payout_mode = fields.Selection(
        selection=[
            ('entry', 'Journal Entry'),
            ('bill', 'Vendor Bill'),
        ],
        string='Payout', required=True,
        default=lambda self: self.env.company.era_commission_payout_mode or 'entry')
    date_start = fields.Date(string='Start Date')
    date_end = fields.Date(string='End Date')
    note = fields.Html(string='Notes')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    amount_draft = fields.Monetary(
        string='Draft', compute='_compute_amounts', currency_field='currency_id')
    amount_confirmed = fields.Monetary(
        string='To Settle', compute='_compute_amounts', currency_field='currency_id')
    amount_settled = fields.Monetary(
        string='Settled', compute='_compute_amounts', currency_field='currency_id')
    line_count = fields.Integer(string='# Lines', compute='_compute_amounts')
    settlement_count = fields.Integer(
        string='# Settlements', compute='_compute_settlement_count')

    _user_uniq = models.Constraint(
        'unique(user_id)',
        'Two agents cannot share the same user: a document would not know which '
        'of them earned the commission.')
    _code_uniq = models.Constraint(
        'unique(code)', 'An agent reference must be unique.')

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for agent in self:
            agent.display_name = (
                f'[{agent.code}] {agent.name}'
                if agent.code and agent.code != '/' else agent.name)

    def _compute_amounts(self):
        groups = self.env['era.commission.line']._read_group(
            [('agent_id', 'in', self.ids), ('state', '!=', 'cancel')],
            ['agent_id', 'state'], ['commission_amount:sum', '__count'])
        per_agent = {}
        for agent, state, amount, count in groups:
            bucket = per_agent.setdefault(agent.id, {'count': 0})
            bucket[state] = amount
            bucket['count'] += count
        for agent in self:
            bucket = per_agent.get(agent.id, {})
            agent.amount_draft = bucket.get('draft', 0.0)
            agent.amount_confirmed = bucket.get('confirmed', 0.0)
            agent.amount_settled = bucket.get('settled', 0.0)
            agent.line_count = bucket.get('count', 0)

    def _compute_settlement_count(self):
        groups = self.env['era.commission.settlement']._read_group(
            [('agent_id', 'in', self.ids)], ['agent_id'], ['__count'])
        counts = {agent.id: count for agent, count in groups}
        for agent in self:
            agent.settlement_count = counts.get(agent.id, 0)

    @api.constrains('parent_id')
    def _check_hierarchy(self):
        if self._has_cycle():
            raise ValidationError(self.env._(
                "An agent cannot be their own manager, directly or through their "
                "team."))

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for agent in self:
            if agent.date_start and agent.date_end and agent.date_end < agent.date_start:
                raise ValidationError(agent.env._(
                    "%(name)s ends before it starts.", name=agent.name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code', '/') == '/':
                vals['code'] = self.env['ir.sequence'].next_by_code(
                    'era.commission.agent') or '/'
        return super().create(vals_list)

    @api.model
    def _agent_for(self, partner, user):
        """Who sells to that customer, in the order the business reads it.

        The customer's own agent first -- an account is followed by one person
        whoever encodes the order -- then the agent behind the salesperson of
        the document.
        """
        if partner:
            agent = partner.era_agent_id or partner.commercial_partner_id.era_agent_id
            if agent:
                return agent
        if user:
            return self.search([('user_id', '=', user.id)], limit=1)
        return self.browse()

    def _is_active_on(self, date):
        """Is the agent on the payroll on that date."""
        self.ensure_one()
        if self.date_start and date < self.date_start:
            return False
        if self.date_end and date > self.date_end:
            return False
        return True

    def _get_assignments(self, date, plan=None):
        """The plan assignments of this agent in force on ``date``."""
        self.ensure_one()
        assignments = self.assignment_ids.filtered(
            lambda assignment: (
                (not assignment.date_from or assignment.date_from <= date)
                and (not assignment.date_to or assignment.date_to >= date)
                and assignment.plan_id.state == 'approved'
            ))
        if plan is not None:
            assignments = assignments.filtered(
                lambda assignment: assignment.plan_id == plan)
        return assignments

    def action_view_commission_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Commission Lines'),
            'res_model': 'era.commission.line',
            'view_mode': 'list,pivot,graph,form',
            'domain': [('agent_id', '=', self.id)],
            'context': {'search_default_group_by_plan': 1},
        }

    def action_view_settlements(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Commission Settlements'),
            'res_model': 'era.commission.settlement',
            'view_mode': 'list,form',
            'domain': [('agent_id', '=', self.id)],
            'context': {'default_agent_id': self.id},
        }


class EraCommissionAgentPlan(models.Model):
    """Which plan pays an agent, over which period, for which share of it."""

    _name = 'era.commission.agent.plan'
    _description = 'Commission Agent Plan Assignment'
    _order = 'agent_id, date_from desc, id desc'
    _rec_name = 'plan_id'

    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent', required=True,
        ondelete='cascade', index=True)
    plan_id = fields.Many2one(
        'era.commission.plan', string='Plan', required=True,
        ondelete='restrict', index=True)
    date_from = fields.Date(string='From')
    date_to = fields.Date(string='To')
    share = fields.Float(
        string='Share (%)', default=100.0, required=True, digits='Discount',
        help="The part of the sale credited to this agent under this plan. Two "
             "agents on the same deal usually add up to 100.")
    company_id = fields.Many2one(related='agent_id.company_id', store=True)

    @api.constrains('date_from', 'date_to', 'plan_id', 'agent_id')
    def _check_no_overlap(self):
        for assignment in self:
            if assignment.date_from and assignment.date_to \
                    and assignment.date_to < assignment.date_from:
                raise ValidationError(assignment.env._(
                    "The assignment of %(plan)s ends before it starts.",
                    plan=assignment.plan_id.name))
            others = self.search([
                ('id', '!=', assignment.id),
                ('agent_id', '=', assignment.agent_id.id),
                ('plan_id', '=', assignment.plan_id.id),
            ])
            for other in others:
                starts_after = (
                    assignment.date_from and other.date_to
                    and assignment.date_from > other.date_to)
                ends_before = (
                    assignment.date_to and other.date_from
                    and assignment.date_to < other.date_from)
                if not starts_after and not ends_before:
                    raise ValidationError(assignment.env._(
                        "%(agent)s is already on %(plan)s over an overlapping "
                        "period. Close the first assignment before opening a "
                        "second one, otherwise the same sale would be paid twice.",
                        agent=assignment.agent_id.name,
                        plan=assignment.plan_id.name))

    @api.constrains('share')
    def _check_share(self):
        for assignment in self:
            if assignment.share <= 0 or assignment.share > 100:
                raise ValidationError(assignment.env._(
                    "A share is a percentage between 0 and 100."))
