from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class EraCommissionPlan(models.Model):
    """What earns a commission, on what amount, and over which period.

    The plan answers three questions: *when* the commission is earned (basis),
    *on what amount* (base and exclusions) and *how much* (its rules). An agent
    is attached to a plan through ``era.commission.agent.plan``.
    """

    _name = 'era.commission.plan'
    _description = 'Commission Plan'
    _inherit = ['mail.thread']
    _order = 'sequence, id'

    name = fields.Char(string='Name', required=True, tracking=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    basis = fields.Selection(
        selection=[
            ('order', 'Confirmed Sales Orders'),
            ('sales', 'Sales (Posted Invoices)'),
            ('collection', 'Collection'),
            ('qty_sold', 'Quantity Sold'),
            ('qty_collected', 'Quantity Collected'),
            ('margin', 'Invoiced Margin'),
        ],
        string='Earned On', default='sales', required=True, tracking=True,
        help="Confirmed Sales Orders: as soon as the order is confirmed.\n"
             "Sales: when the invoice is posted.\n"
             "Collection: only on what the customer actually paid, in "
             "proportion to each payment.\n"
             "Quantity Sold: on the net units invoiced over the period, one "
             "line per product.\n"
             "Quantity Collected: the same units, in proportion to what the "
             "customer has paid.\n"
             "Invoiced Margin: on the invoiced amount less the cost of the goods.")
    amount_base = fields.Selection(
        selection=[
            ('untaxed', 'Untaxed Amount'),
            ('total', 'Total With Taxes'),
        ],
        string='Amount', default='untaxed', required=True,
        help="Tax is money collected for the government, so the untaxed amount is "
             "the usual base.")
    exclude_downpayment = fields.Boolean(
        string='Exclude Down Payments', default=True,
        help="A down payment line is an advance on the same order, not a second "
             "sale. Leaving it in pays the commission twice.")
    excluded_product_ids = fields.Many2many(
        'product.product', 'era_commission_plan_excluded_product_rel',
        'plan_id', 'product_id', string='Excluded Products',
        help="Shipping, handling and other pass-through products the agent did "
             "not sell.")
    deduct_refunds = fields.Boolean(
        string='Deduct Credit Notes', default=True,
        help="A credit note produces a negative commission line, so a returned "
             "sale is taken back off the agent.")
    periodicity = fields.Selection(
        selection=[
            ('month', 'Monthly'),
            ('quarter', 'Quarterly'),
            ('year', 'Yearly'),
        ],
        string='Periodicity', default='month', required=True,
        help="The period a settlement covers by default, and the period a target "
             "is measured over.")
    date_from = fields.Date(string='Valid From')
    date_to = fields.Date(string='Valid To')
    deduct_tax = fields.Boolean(
        string='Deduct Tax', default=True,
        help="Take the tax off the base before applying the rate. The base is "
             "then collected with the tax in it and the tax is kept beside it, "
             "so base less tax is the net the rate is applied to.")
    tax_method = fields.Selection(
        selection=[
            ('actual', 'Actual Tax of the Document'),
            ('divide', 'Divide by the Tax Rate'),
        ],
        string='Tax Method', default='actual', required=True,
        help="Actual: the tax the invoice really carries, line by line.\n"
             "Divide: the base less the base divided by one plus the commission "
             "tax rate set on the company.")
    use_target = fields.Boolean(
        string='Use Targets',
        help="Take the agent's target of the period into account.")
    target_mode = fields.Selection(
        selection=[
            ('deduct', 'Deduct the Target from the Base'),
            ('factor', 'Multiply by an Achievement Factor'),
        ],
        string='Target Mode', default='deduct', required=True,
        help="Deduct: the target is taken off the base before the rate, spread "
             "over the lines of the period in proportion to their base.\n"
             "Multiply: the whole commission of the period is multiplied by the "
             "factor of the tier the agent reached.\n"
             "Never both: that would charge the shortfall twice.")
    target_tier_ids = fields.One2many(
        'era.commission.plan.target.tier', 'plan_id', string='Achievement Tiers')
    rule_ids = fields.One2many(
        'era.commission.rule', 'plan_id', string='Rules')
    assignment_ids = fields.One2many(
        'era.commission.agent.plan', 'plan_id', string='Agents')
    agent_count = fields.Integer(string='# Agents', compute='_compute_agent_count')
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('approved', 'Approved'),
            ('closed', 'Closed'),
        ],
        string='Status', default='draft', required=True, tracking=True, copy=False,
        help="Only an approved plan is computed. Draft is a plan being written, "
             "closed is one that no longer earns anything.")
    note = fields.Html(string='Notes')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    # Changing any of these on a live plan would silently repay a period that
    # has already been paid, so they are frozen once money went out on the plan.
    _FROZEN_FIELDS = (
        'basis', 'amount_base', 'exclude_downpayment', 'excluded_product_ids',
        'deduct_refunds', 'date_from', 'date_to', 'use_target', 'company_id',
        'deduct_tax', 'tax_method', 'target_mode',
    )

    #: How a basis is read as one of the four commission types the business
    #: names. An order, an invoice and a margin are all an amount sold.
    _BASIS_COMMISSION_TYPE = {
        'order': 'sales',
        'sales': 'sales',
        'margin': 'sales',
        'collection': 'collection',
        'qty_sold': 'qty_sold',
        'qty_collected': 'qty_collected',
    }

    def _commission_type(self):
        self.ensure_one()
        return self._BASIS_COMMISSION_TYPE.get(self.basis, 'sales')

    def _compute_agent_count(self):
        groups = self.env['era.commission.agent.plan']._read_group(
            [('plan_id', 'in', self.ids)], ['plan_id'], ['__count'])
        counts = {plan.id: count for plan, count in groups}
        for plan in self:
            plan.agent_count = counts.get(plan.id, 0)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for plan in self:
            if plan.date_from and plan.date_to and plan.date_to < plan.date_from:
                raise ValidationError(plan.env._(
                    "%(name)s ends before it starts.", name=plan.name))

    def _has_settled_lines(self):
        self.ensure_one()
        return bool(self.env['era.commission.line'].search_count([
            ('plan_id', '=', self.id),
            ('state', 'in', ('confirmed', 'settled')),
        ], limit=1))

    def write(self, vals):
        """Refuse to rewrite the arithmetic of a plan that has already paid.

        A settled line is a line an agent has been paid for. Changing the basis
        or the period under it would make the settlement unreproducible: the
        printed statement and a recomputation would no longer agree.
        """
        touched = [name for name in vals if name in self._FROZEN_FIELDS]
        if touched:
            for plan in self:
                if plan.state != 'draft' and plan._has_settled_lines():
                    raise UserError(plan.env._(
                        "%(name)s already produced commission lines that were "
                        "confirmed or settled. Close it and open a new plan "
                        "instead of rewriting how it computes: a paid statement "
                        "has to stay reproducible.", name=plan.name))
        return super().write(vals)

    def _has_agent_rates(self):
        """Do the agents on this plan carry a rate of their own.

        A plan with no rule is the normal shape now: the percentage lives on the
        agent, not on the plan. It is only empty -- and refused -- when neither
        side has anything to say.
        """
        self.ensure_one()
        agents = self.assignment_ids.agent_id
        if not agents:
            return False
        commission_type = self._commission_type()
        rates = self.env['era.commission.agent.rate'].search([
            ('agent_id', 'in', agents.ids),
            ('commission_type', '=', commission_type),
        ])
        if any(rate.rate or rate.unit_price for rate in rates):
            return True
        if commission_type in ('qty_sold', 'qty_collected'):
            # a unit price can also come from a tier or from the product
            return bool(self.env['era.commission.unit.price.tier'].search_count(
                [('company_id', '=', self.company_id.id)], limit=1))
        return False

    def action_approve(self):
        for plan in self:
            if not plan.rule_ids and not plan._has_agent_rates():
                raise UserError(plan.env._(
                    "%(name)s has neither a rule nor an agent carrying a rate "
                    "for %(type)s, so it would compute nothing. Either write a "
                    "rule on the plan, or set the rate on the agents assigned "
                    "to it.",
                    name=plan.name,
                    type=dict(plan._fields['basis'].selection)[plan.basis]))
            plan.state = 'approved'

    def action_draft(self):
        for plan in self:
            if plan._has_settled_lines():
                raise UserError(plan.env._(
                    "%(name)s has settled commission lines and cannot go back to "
                    "draft.", name=plan.name))
            plan.state = 'draft'

    def action_close(self):
        self.state = 'closed'

    def _is_active_on(self, date):
        self.ensure_one()
        if self.state != 'approved':
            return False
        if self.date_from and date < self.date_from:
            return False
        if self.date_to and date > self.date_to:
            return False
        return True

    def _get_target_factor(self, achievement_rate):
        """The factor, as a ratio, matching an achievement percentage.

        Tiers are read as thresholds: the highest tier whose ``achievement_from``
        is reached wins. With no tier configured nothing is scaled.
        """
        self.ensure_one()
        if not self.use_target or self.target_mode != 'factor' \
                or not self.target_tier_ids:
            return 1.0
        matched = self.target_tier_ids.filtered(
            lambda tier: achievement_rate >= tier.achievement_from
        ).sorted('achievement_from')
        if not matched:
            return 0.0
        return matched[-1].factor / 100.0


class EraCommissionPlanTargetTier(models.Model):
    _name = 'era.commission.plan.target.tier'
    _description = 'Commission Target Achievement Tier'
    _order = 'plan_id, achievement_from'

    plan_id = fields.Many2one(
        'era.commission.plan', string='Plan', required=True,
        ondelete='cascade', index=True)
    achievement_from = fields.Float(
        string='Achievement From (%)', required=True, digits='Discount',
        help="Reached share of the target from which this factor applies.")
    factor = fields.Float(
        string='Factor (%)', required=True, default=100.0, digits='Discount',
        help="What the period's commission is multiplied by. 100 pays it in "
             "full, 50 halves it, 110 adds a bonus.")
    company_id = fields.Many2one(related='plan_id.company_id', store=True)

    @api.constrains('achievement_from', 'factor')
    def _check_values(self):
        for tier in self:
            if tier.achievement_from < 0 or tier.factor < 0:
                raise ValidationError(tier.env._(
                    "An achievement threshold and a factor cannot be negative."))

    @api.depends('achievement_from', 'factor')
    def _compute_display_name(self):
        for tier in self:
            tier.display_name = f'≥ {tier.achievement_from:g}% → {tier.factor:g}%'
