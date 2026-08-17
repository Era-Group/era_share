from odoo import api, fields, models
from odoo.exceptions import ValidationError

#: The four things a commission is earned on. ``adjustment`` is deliberately
#: absent: a manual settlement line is never a configured rate.
COMMISSION_TYPES = [
    ('sales', 'Commission on Sales'),
    ('collection', 'Commission on Collection'),
    ('qty_sold', 'Commission on Quantity Sold'),
    ('qty_collected', 'Commission on Quantity Collected'),
]

#: What a commission line can be, the four above plus the manual one.
LINE_COMMISSION_TYPES = COMMISSION_TYPES + [('adjustment', 'Manual Adjustment')]


class EraCommissionAgentRate(models.Model):
    """The rate, or the unit price, this agent earns on this kind of commission.

    One row per agent and per commission type, optionally narrowed to a single
    product. Nothing constrains the rate to a list of blessed percentages: the
    business asked to be able to type any figure, and a percentage nobody can
    type is a percentage that gets worked around in a spreadsheet.
    """

    _name = 'era.commission.agent.rate'
    _description = 'Commission Agent Rate'
    _order = 'agent_id, commission_type, product_id'
    _rec_name = 'agent_id'

    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent', required=True,
        ondelete='cascade', index=True)
    commission_type = fields.Selection(
        selection=COMMISSION_TYPES, string='Commission Type', required=True,
        index=True)
    rate = fields.Float(
        string='Rate (%)', digits='Discount',
        help="Applied to the net base of a commission on sales or on "
             "collection. Any value: the business decides the percentage.")
    unit_price = fields.Monetary(
        string='Unit Price', currency_field='currency_id',
        help="What one unit earns on a quantity commission, when no unit price "
             "tier covers the quantity.")
    product_id = fields.Many2one(
        'product.product', string='Product', ondelete='cascade',
        index='btree_not_null',
        help="Leave empty for the rate this agent earns on everything. Set it "
             "to give this agent a different figure on one product.")
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    _type_uniq = models.Constraint(
        'unique(agent_id, commission_type, product_id, company_id)',
        'An agent has a single rate per commission type and product.')

    @api.depends('agent_id', 'commission_type', 'product_id')
    def _compute_display_name(self):
        types = dict(COMMISSION_TYPES)
        for rate in self:
            label = types.get(rate.commission_type, '')
            if rate.product_id:
                label = f'{label} - {rate.product_id.display_name}'
            rate.display_name = f'{rate.agent_id.display_name}: {label}'

    @api.constrains('rate', 'unit_price')
    def _check_values(self):
        for rate in self:
            if rate.rate < 0 or rate.unit_price < 0:
                raise ValidationError(rate.env._(
                    "A commission rate and a unit price cannot be negative."))

    @api.constrains('agent_id', 'commission_type', 'product_id', 'company_id')
    def _check_unique_without_product(self):
        """Postgres treats two NULL products as different; the business does not."""
        for rate in self.filtered(lambda rate: not rate.product_id):
            duplicate = self.search_count([
                ('id', '!=', rate.id),
                ('agent_id', '=', rate.agent_id.id),
                ('commission_type', '=', rate.commission_type),
                ('product_id', '=', False),
                ('company_id', '=', rate.company_id.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(rate.env._(
                    "%(agent)s already has a rate for this commission type. "
                    "Edit it instead of adding a second one.",
                    agent=rate.agent_id.name))

    @api.model
    def _rate_for(self, agent, commission_type, product=None):
        """``(rate, unit_price)`` for that agent, most specific first.

        Agent + type + product wins over agent + type. Nothing on file is
        ``(0, 0)``: the officer fills the figure in on the line itself.
        """
        if not agent or not commission_type:
            return 0.0, 0.0
        domain = [
            ('agent_id', '=', agent.id),
            ('commission_type', '=', commission_type),
        ]
        if product:
            specific = self.search(
                domain + [('product_id', '=', product.id)], limit=1)
            if specific:
                return specific.rate, specific.unit_price
        general = self.search(domain + [('product_id', '=', False)], limit=1)
        if general:
            return general.rate, general.unit_price
        return 0.0, 0.0
