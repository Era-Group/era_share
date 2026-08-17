from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EraCommissionRule(models.Model):
    """One line of a plan: what it applies to, and how much it pays.

    The rules of a plan are read in ``sequence`` order and **the first one that
    matches wins**. They are never added up on the same sale: a deterministic
    rule is one a salesperson can check on paper. Stacking two payments on the
    same line is done by putting the agent on a second plan.
    """

    _name = 'era.commission.rule'
    _description = 'Commission Rule'
    _order = 'plan_id, sequence, id'

    name = fields.Char(string='Description', required=True)
    sequence = fields.Integer(
        string='Sequence', default=10,
        help="Rules are tried from top to bottom and the first match is applied. "
             "Put the most specific rule first.")
    plan_id = fields.Many2one(
        'era.commission.plan', string='Plan', required=True,
        ondelete='cascade', index=True)
    active = fields.Boolean(string='Active', default=True)

    # --- what the rule applies to; an empty filter means "any" -----------
    product_id = fields.Many2one('product.product', string='Product')
    product_categ_id = fields.Many2one(
        'product.category', string='Product Category',
        help="Matches the category and everything under it.")
    product_tag_ids = fields.Many2many(
        'product.tag', 'era_commission_rule_product_tag_rel',
        'rule_id', 'tag_id', string='Product Tags',
        help="Matches when the product carries at least one of these tags.")
    partner_id = fields.Many2one('res.partner', string='Customer')
    partner_category_id = fields.Many2one(
        'res.partner.category', string='Customer Tag')
    country_id = fields.Many2one('res.country', string='Country')
    state_id = fields.Many2one(
        'res.country.state', string='Region',
        domain="[('country_id', '=?', country_id)]")
    team_id = fields.Many2one('crm.team', string='Sales Team')
    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent',
        help="Restrict this rule to a single agent of the plan.")
    min_base = fields.Monetary(
        string='Minimum Amount', currency_field='currency_id',
        help="The rule is skipped below this amount, so the next rule gets its "
             "chance.")

    # --- how much it pays ------------------------------------------------
    base_field = fields.Selection(
        selection=[
            ('amount', 'Sale Amount'),
            ('margin', 'Margin'),
        ],
        string='Computed On', default='amount', required=True,
        help="Sale Amount uses the base of the plan. Margin uses the sale amount "
             "less the cost of the goods, whatever the plan is earned on.")
    calc_type = fields.Selection(
        selection=[
            ('percent', 'Percentage'),
            ('tier', 'Tiers'),
            ('fixed_qty', 'Fixed per Unit'),
            ('fixed_doc', 'Fixed per Document'),
        ],
        string='Computation', default='percent', required=True)
    rate = fields.Float(
        string='Rate (%)', digits='Discount',
        help="Applied to the computation base when the computation is a "
             "percentage.")
    amount_fixed = fields.Monetary(
        string='Fixed Amount', currency_field='currency_id')
    tier_ids = fields.One2many(
        'era.commission.rule.tier', 'rule_id', string='Tiers')
    tier_mode = fields.Selection(
        selection=[
            ('progressive', 'Progressive'),
            ('flat', 'Whole Amount'),
        ],
        string='Tier Mode', default='progressive', required=True,
        help="Progressive: each slice is paid at its own rate, like an income "
             "tax scale.\nWhole Amount: the rate of the reached tier applies to "
             "the whole amount.")
    amount_max = fields.Monetary(
        string='Cap', currency_field='currency_id',
        help="Highest commission this rule pays on one line. Leave at zero for "
             "no cap.")
    company_id = fields.Many2one(related='plan_id.company_id', store=True)
    currency_id = fields.Many2one(related='plan_id.currency_id')

    @api.constrains('rate', 'amount_fixed', 'amount_max', 'calc_type', 'tier_ids')
    def _check_amounts(self):
        for rule in self:
            if rule.rate < 0 or rule.amount_fixed < 0 or rule.amount_max < 0:
                raise ValidationError(rule.env._(
                    "A rate, a fixed amount and a cap cannot be negative."))
            if rule.calc_type == 'tier' and not rule.tier_ids:
                raise ValidationError(rule.env._(
                    "%(name)s computes on tiers but has none.", name=rule.name))

    def _match(self, occurrence):
        """Does this rule cover that occurrence."""
        self.ensure_one()
        product = occurrence.get('product')
        partner = occurrence.get('partner')
        if self.product_id and product != self.product_id:
            return False
        if self.product_categ_id:
            categ = product.categ_id if product else False
            if not categ or self.product_categ_id not in self._categ_and_parents(categ):
                return False
        if self.product_tag_ids:
            tags = product.all_product_tag_ids if product else False
            if not tags or not (tags & self.product_tag_ids):
                return False
        if self.partner_id and partner != self.partner_id:
            return False
        if self.partner_category_id:
            if not partner or self.partner_category_id not in partner.category_id:
                return False
        if self.country_id and (not partner or partner.country_id != self.country_id):
            return False
        if self.state_id and (not partner or partner.state_id != self.state_id):
            return False
        if self.team_id and occurrence.get('team') != self.team_id:
            return False
        if self.agent_id and occurrence.get('agent') != self.agent_id:
            return False
        if self.min_base and abs(self._rule_base(occurrence)) < self.min_base:
            return False
        return True

    @api.model
    def _categ_and_parents(self, categ):
        parents = categ.browse()
        while categ:
            parents |= categ
            categ = categ.parent_id
        return parents

    def _rule_base(self, occurrence):
        """The figure this rule computes on."""
        self.ensure_one()
        if self.base_field == 'margin':
            return occurrence.get('margin', 0.0)
        return occurrence.get('base_amount', 0.0)

    def _compute_amount(self, base, quantity=0.0, rate=None):
        """The commission this rule pays on ``base``.

        A negative base -- a credit note, a refunded collection -- is computed on
        its absolute value and given its sign back, so a return takes back
        exactly what the sale paid.

        ``rate`` overrides the rule's own percentage. That is how the rate an
        officer corrects on a commission line, or the rate carried by the agent,
        wins over the rate written on the plan -- while the tiers, the fixed
        amounts and the cap stay the rule's business.
        """
        self.ensure_one()
        sign = -1.0 if base < 0 else 1.0
        base = abs(base)
        quantity = abs(quantity)
        if self.calc_type == 'percent':
            amount = base * (self.rate if rate is None else rate) / 100.0
        elif self.calc_type == 'fixed_qty':
            amount = self.amount_fixed * quantity
        elif self.calc_type == 'fixed_doc':
            amount = self.amount_fixed
        else:
            amount = self._compute_tier_amount(base)
        if self.amount_max and amount > self.amount_max:
            amount = self.amount_max
        return sign * amount

    def _compute_tier_amount(self, base):
        self.ensure_one()
        tiers = self.tier_ids.sorted('base_from')
        if not tiers:
            return 0.0
        if self.tier_mode == 'flat':
            reached = tiers.filtered(lambda tier: base >= tier.base_from)
            return base * reached[-1].rate / 100.0 if reached else 0.0
        amount = 0.0
        for index, tier in enumerate(tiers):
            if base <= tier.base_from:
                break
            upper = tiers[index + 1].base_from if index + 1 < len(tiers) else base
            slice_amount = min(base, upper) - tier.base_from
            if slice_amount > 0:
                amount += slice_amount * tier.rate / 100.0
        return amount

    def _effective_rate(self, base, amount):
        """The rate actually paid, for the audit trail and the printed statement."""
        self.ensure_one()
        if self.calc_type == 'percent':
            return self.rate
        return (amount / base * 100.0) if base else 0.0


class EraCommissionRuleTier(models.Model):
    _name = 'era.commission.rule.tier'
    _description = 'Commission Rule Tier'
    _order = 'rule_id, base_from'

    rule_id = fields.Many2one(
        'era.commission.rule', string='Rule', required=True,
        ondelete='cascade', index=True)
    base_from = fields.Monetary(
        string='From', required=True, currency_field='currency_id',
        help="Amount from which this tier's rate applies. The first tier usually "
             "starts at zero.")
    rate = fields.Float(string='Rate (%)', required=True, digits='Discount')
    company_id = fields.Many2one(related='rule_id.company_id', store=True)
    currency_id = fields.Many2one(related='rule_id.currency_id')

    @api.constrains('base_from', 'rate')
    def _check_values(self):
        for tier in self:
            if tier.base_from < 0 or tier.rate < 0:
                raise ValidationError(tier.env._(
                    "A tier threshold and a tier rate cannot be negative."))

    @api.depends('base_from', 'rate')
    def _compute_display_name(self):
        for tier in self:
            tier.display_name = f'≥ {tier.base_from:g} → {tier.rate:g}%'
