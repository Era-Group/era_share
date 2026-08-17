from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .commission_agent_rate import COMMISSION_TYPES


class EraCommissionTarget(models.Model):
    """What an agent was asked to reach over a period, and what they reached.

    The target is central: it belongs to an agent and a commission type, not to
    a plan, because the same agent is usually on one target whichever plan the
    money came through. A plan may still be named to narrow it.

    It is used one of two ways, and never both -- see ``target_mode`` on the
    plan. Deducted, it is taken off the base before the rate, spread over the
    lines of the period in proportion to their base. As a factor, it turns into
    an achievement percentage that multiplies the whole commission of the
    period. With no target on file nothing is deducted and the factor is 100%:
    a missing target never costs an agent money.
    """

    _name = 'era.commission.target'
    _description = 'Commission Target'
    _order = 'date_from desc, agent_id'
    _rec_name = 'agent_id'

    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent', required=True,
        ondelete='cascade', index=True)
    commission_type = fields.Selection(
        selection=COMMISSION_TYPES, string='Commission Type', required=True,
        default='sales', index=True,
        help="Which of the agent's commissions this target applies to.")
    plan_id = fields.Many2one(
        'era.commission.plan', string='Plan', ondelete='cascade',
        index='btree_not_null',
        help="Leave empty for a target that covers every plan of that "
             "commission type.")
    product_id = fields.Many2one(
        'product.product', string='Product', ondelete='cascade',
        index='btree_not_null',
        help="Leave empty for a target on everything the agent sells.")
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    target_amount = fields.Monetary(
        string='Target', currency_field='currency_id',
        help="The amount expected over the period, on the same base the plan "
             "computes on.")
    target_qty = fields.Float(
        string='Target Quantity', digits='Product Unit',
        help="The number of units expected over the period, for a quantity "
             "commission.")
    achieved_amount = fields.Monetary(
        string='Achieved', compute='_compute_achievement', store=True,
        currency_field='currency_id')
    achievement_rate = fields.Float(
        string='Achievement (%)', compute='_compute_achievement', store=True,
        digits='Discount')
    factor = fields.Float(
        string='Factor (%)', compute='_compute_achievement', store=True,
        digits='Discount',
        help="What the commission of the period is multiplied by, read from the "
             "plan's achievement tiers. Only used when the plan deals with its "
             "target as a factor.")
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    @api.depends('agent_id', 'commission_type', 'date_from', 'date_to')
    def _compute_display_name(self):
        types = dict(COMMISSION_TYPES)
        for target in self:
            target.display_name = (
                f'{target.agent_id.display_name or "-"} - '
                f'{types.get(target.commission_type, "")} '
                f'({target.date_from or "?"} → {target.date_to or "?"})')

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for target in self:
            if target.date_to < target.date_from:
                raise ValidationError(target.env._(
                    "The target period ends before it starts."))

    @api.constrains('target_amount', 'target_qty')
    def _check_target(self):
        for target in self:
            if target.target_amount < 0 or target.target_qty < 0:
                raise ValidationError(target.env._(
                    "A target cannot be negative."))
            if not target.target_amount and not target.target_qty:
                raise ValidationError(target.env._(
                    "Set either an amount or a quantity: a target of nothing "
                    "deducts nothing and measures nothing."))

    @api.constrains('agent_id', 'commission_type', 'product_id', 'plan_id',
                    'date_from', 'date_to')
    def _check_no_overlap(self):
        """Two targets covering the same day for the same thing cannot both apply."""
        for target in self:
            overlapping = self.search([
                ('id', '!=', target.id),
                ('agent_id', '=', target.agent_id.id),
                ('commission_type', '=', target.commission_type),
                ('product_id', '=', target.product_id.id),
                ('plan_id', '=', target.plan_id.id),
                ('company_id', '=', target.company_id.id),
                ('date_from', '<=', target.date_to),
                ('date_to', '>=', target.date_from),
            ], limit=1)
            if overlapping:
                raise ValidationError(target.env._(
                    "%(agent)s already has a target of this kind over an "
                    "overlapping period. One period has to name one target.",
                    agent=target.agent_id.name))

    def _line_domain(self):
        self.ensure_one()
        domain = [
            ('agent_id', '=', self.agent_id.id),
            ('commission_type', '=', self.commission_type),
            ('line_type', '=', 'sale'),
            ('state', '!=', 'cancel'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]
        if self.plan_id:
            domain.append(('plan_id', '=', self.plan_id.id))
        if self.product_id:
            domain.append(('product_id', '=', self.product_id.id))
        return domain

    @api.depends('agent_id', 'commission_type', 'plan_id', 'product_id',
                 'date_from', 'date_to', 'target_amount', 'target_qty',
                 'plan_id.target_tier_ids.achievement_from',
                 'plan_id.target_tier_ids.factor')
    def _compute_achievement(self):
        for target in self:
            achieved = quantity = 0.0
            if target.agent_id and target.date_from and target.date_to:
                groups = self.env['era.commission.line']._read_group(
                    target._line_domain(), [], ['base_amount:sum', 'quantity:sum'])
                if groups:
                    achieved, quantity = groups[0][0] or 0.0, groups[0][1] or 0.0
            on_quantity = bool(target.target_qty) and not target.target_amount
            reached = quantity if on_quantity else achieved
            expected = target.target_qty if on_quantity else target.target_amount
            target.achieved_amount = achieved
            rate = (reached / expected * 100.0) if expected else 0.0
            target.achievement_rate = rate
            target.factor = (
                target.plan_id._get_target_factor(rate) * 100.0
                if target.plan_id else 100.0)

    def action_recompute(self):
        self._compute_achievement()
        return True

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Commission Lines'),
            'res_model': 'era.commission.line',
            'view_mode': 'list,pivot,form',
            'domain': self._line_domain(),
        }

    # ------------------------------------------------------------------
    @api.model
    def _target_for(self, agent, commission_type, date_from, date_to,
                    product=None, plan=None):
        """The target covering a whole period, most specific first.

        A target applies when it covers the settlement period end to end, so a
        yearly target still applies to a monthly run. Agent + type + product
        wins over agent + type, and a target naming the plan wins over one that
        does not.
        """
        if not agent or not commission_type:
            return self.browse()
        base = [
            ('agent_id', '=', agent.id),
            ('commission_type', '=', commission_type),
            ('date_from', '<=', date_from),
            ('date_to', '>=', date_to),
            ('company_id', 'in', self.env.companies.ids),
        ]
        plan_filters = [[('plan_id', '=', plan.id)], [('plan_id', '=', False)]] \
            if plan else [[('plan_id', '=', False)]]
        product_filters = [[('product_id', '=', product.id)]] if product else []
        product_filters.append([('product_id', '=', False)])
        for product_filter in product_filters:
            for plan_filter in plan_filters:
                target = self.search(
                    base + product_filter + plan_filter,
                    order='date_to desc, date_from asc', limit=1)
                if target:
                    return target
        return self.browse()

    @api.model
    def _get_factor(self, agent, plan, date_from, date_to):
        """The factor to apply to a settlement period, 1.0 when no target rules."""
        if not plan.use_target or plan.target_mode != 'factor':
            return 1.0
        target = self._target_for(
            agent, plan._commission_type(), date_from, date_to, plan=plan)
        if not target:
            return 1.0
        # read the tiers off the plan that asked, not off the target: a central
        # target carries no plan and so has no tiers of its own.
        return plan._get_target_factor(target.achievement_rate)
