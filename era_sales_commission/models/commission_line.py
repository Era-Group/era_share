from odoo import api, fields, models
from odoo.exceptions import UserError

from .commission_agent_rate import LINE_COMMISSION_TYPES


class EraCommissionLine(models.Model):
    """One earned amount, stored, with everything needed to explain it.

    Stored -- not a SQL view -- for one reason: once an agent has been paid, the
    line has to keep saying what was paid, whatever happens later to the order
    or the invoice behind it. A recomputation only ever touches draft lines; a
    document cancelled after payment produces a ``reversal`` line instead, so
    the claw-back is a document of its own rather than a silent edit.

    The commission itself is computed from what the line carries -- base, tax,
    target, rate or unit price -- so an officer who corrects a percentage on the
    line sees the amount follow immediately, without re-running the engine.
    """

    _name = 'era.commission.line'
    _description = 'Commission Line'
    _order = 'date desc, id desc'

    name = fields.Char(string='Description')
    date = fields.Date(string='Date', required=True, index=True)
    date_from = fields.Date(
        string='Period From',
        help="Set on a quantity commission, which is one line per product and "
             "per period rather than one line per invoice line.")
    date_to = fields.Date(string='Period To')
    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent', required=True,
        ondelete='restrict', index=True)
    plan_id = fields.Many2one(
        'era.commission.plan', string='Plan', ondelete='restrict', index=True)
    rule_id = fields.Many2one(
        'era.commission.rule', string='Rule', ondelete='restrict')
    commission_type = fields.Selection(
        selection=LINE_COMMISSION_TYPES, string='Commission Type',
        default='sales', required=True, index=True,
        help="What this line was earned on: an amount sold, an amount "
             "collected, a quantity sold, a quantity collected, or a manual "
             "adjustment.")
    line_type = fields.Selection(
        selection=[
            ('sale', 'Sale'),
            ('override', 'Override'),
            ('adjustment', 'Adjustment'),
            ('reversal', 'Reversal'),
        ],
        string='Type', default='sale', required=True, index=True)
    parent_line_id = fields.Many2one(
        'era.commission.line', string='Team Member Line', ondelete='cascade',
        index='btree_not_null',
        help="The line of the team member this override was computed from.")
    reversed_line_id = fields.Many2one(
        'era.commission.line', string='Reversed Line', ondelete='set null',
        index='btree_not_null')

    partner_id = fields.Many2one('res.partner', string='Customer', index=True)
    product_id = fields.Many2one('product.product', string='Product')
    team_id = fields.Many2one('crm.team', string='Sales Team')

    sale_order_id = fields.Many2one(
        'sale.order', string='Sales Order', ondelete='set null',
        index='btree_not_null')
    sale_line_id = fields.Many2one(
        'sale.order.line', string='Sales Order Line', ondelete='set null',
        index='btree_not_null')
    move_id = fields.Many2one(
        'account.move', string='Invoice', ondelete='set null',
        index='btree_not_null')
    move_line_id = fields.Many2one(
        'account.move.line', string='Invoice Line', ondelete='set null',
        index='btree_not_null')
    partial_id = fields.Many2one(
        'account.partial.reconcile', string='Payment Matching',
        ondelete='set null', index='btree_not_null')
    payment_id = fields.Many2one(
        'account.payment', string='Payment', ondelete='set null',
        index='btree_not_null')

    # --- what the commission is computed from ---------------------------
    quantity = fields.Float(string='Quantity', digits='Product Unit')
    base_amount = fields.Monetary(
        string='Base', currency_field='currency_id',
        help="The amount the commission was computed on, already reduced to this "
             "agent's share of the sale. It includes the tax when the tax is "
             "deducted, so that base less tax is the net, literally.")
    margin_amount = fields.Monetary(
        string='Margin', currency_field='currency_id',
        help="Base less the cost of the goods, for a rule computed on margin.")
    tax_deducted = fields.Monetary(
        string='Tax Deducted', currency_field='currency_id',
        help="The part of the base that is tax and belongs to the government.")
    deduct_tax = fields.Boolean(
        string='Deduct Tax',
        help="Take the tax off the base before applying the rate.")
    tax_method = fields.Selection(
        selection=[
            ('actual', 'Actual Tax of the Document'),
            ('divide', 'Divide by the Tax Rate'),
        ],
        string='Tax Method', default='actual',
        help="Actual: the tax the invoice really carries. Divide: the base "
             "divided by one plus the company's commission tax rate.")
    use_target = fields.Boolean(
        string='Deduct Target',
        help="Take this line's share of the period target off the base.")
    target_amount = fields.Monetary(
        string='Target Deducted', currency_field='currency_id',
        help="This line's share of the agent's target for the period, spread "
             "over the lines in proportion to their base.")
    target_qty = fields.Float(
        string='Target Quantity Deducted', digits='Product Unit',
        help="This line's share of a quantity target for the period.")
    era_refund_amount = fields.Monetary(
        string='Refunds Deducted', currency_field='currency_id',
        help="What credit notes took back off this line, for the record.")
    era_collection_ratio = fields.Float(
        string='Collected (%)', digits='Discount',
        help="How much of the invoices behind this line the customer has paid. "
             "An estimate read off the invoice residual, not a follow-up of one "
             "product inside a payment.")
    share_rate = fields.Float(
        string='Share (%)', default=100.0, digits='Discount',
        help="The part of the sale credited to this agent.")
    rate = fields.Float(string='Rate (%)', digits='Discount')
    unit_price = fields.Monetary(
        string='Unit Price', currency_field='currency_id',
        help="What one unit earns on a quantity commission.")
    manual_amount = fields.Monetary(
        string='Manual Amount', currency_field='currency_id',
        help="The commission of a line nothing computes: an override, a "
             "claw-back, a correction decided by a human.")
    commission_amount = fields.Monetary(
        string='Commission', currency_field='currency_id',
        compute='_compute_commission_amount', store=True, readonly=True)

    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('confirmed', 'To Settle'),
            ('settled', 'Settled'),
            ('cancel', 'Cancelled'),
        ],
        string='Status', default='draft', required=True, index=True)
    settlement_id = fields.Many2one(
        'era.commission.settlement', string='Settlement', ondelete='set null',
        index='btree_not_null', copy=False)
    settlement_state = fields.Selection(
        related='settlement_id.state', string='Settlement Status', store=True)

    origin_key = fields.Char(
        string='Origin Key', required=True, index=True, copy=False,
        help="What makes a recomputation idempotent: the same source document "
             "always produces the same key, so a line is never created twice.")
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    _origin_uniq = models.Constraint(
        'unique(company_id, origin_key)',
        'A commission line already exists for this source document, rule and '
        'agent.')

    def init(self):
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS era_commission_line_agent_date_state_idx
            ON era_commission_line (agent_id, date, state)
        """)

    @api.depends('name', 'agent_id')
    def _compute_display_name(self):
        for line in self:
            line.display_name = line.name or line.agent_id.display_name or '/'

    # ------------------------------------------------------------------
    # the formula
    # ------------------------------------------------------------------
    def _net_base(self):
        """The base after tax and target, with the sign of a return kept.

        A positive line that falls below its share of the target earns nothing;
        a credit note stays negative for exactly what it took back. Those are
        the two rules the business gave, and separating the sign from the size
        is the only way both can hold at once.
        """
        self.ensure_one()
        sign = -1.0 if self.base_amount < 0 else 1.0
        net = abs(self.base_amount)
        if self.deduct_tax:
            net -= abs(self.tax_deducted)
        if self.use_target:
            net -= sign * self.target_amount
        return sign * max(net, 0.0)

    def _rule_base(self):
        """What the matched rule computes on."""
        self.ensure_one()
        if self.rule_id.base_field == 'margin':
            return self.margin_amount
        return self._net_base()

    def _net_quantity(self):
        self.ensure_one()
        sign = -1.0 if self.quantity < 0 else 1.0
        net = abs(self.quantity)
        if self.use_target:
            net -= sign * self.target_qty
        return sign * max(net, 0.0)

    def _commission_value(self):
        """What this line pays, before rounding."""
        self.ensure_one()
        if self.commission_type == 'adjustment':
            return self.manual_amount
        if self.commission_type in ('qty_sold', 'qty_collected'):
            return self._net_quantity() * self.unit_price
        if self.rule_id:
            # Tiers, fixed amounts and caps stay the rule's business; the rate
            # written on the line overrides the rule's only for a percentage.
            return self.rule_id._compute_amount(
                self._rule_base(), self.quantity, rate=self.rate)
        return self._net_base() * self.rate / 100.0

    @api.depends('commission_type', 'base_amount', 'margin_amount', 'deduct_tax',
                 'tax_deducted', 'use_target', 'target_amount', 'target_qty',
                 'rate', 'quantity', 'unit_price', 'manual_amount',
                 'rule_id.calc_type', 'rule_id.base_field', 'rule_id.rate',
                 'rule_id.amount_fixed', 'rule_id.amount_max',
                 'rule_id.tier_mode', 'rule_id.tier_ids.base_from',
                 'rule_id.tier_ids.rate')
    def _compute_commission_amount(self):
        # A settled line is what an agent was paid. Editing the rule it fell
        # under afterwards must never rewrite a printed statement, so its stored
        # amount is read back rather than recomputed.
        frozen = self.filtered(
            lambda line: line.state == 'settled' and isinstance(line.id, int))
        paid = {}
        if frozen:
            self.env.cr.execute(
                "SELECT id, commission_amount FROM era_commission_line "
                "WHERE id IN %s", (tuple(frozen.ids),))
            paid = dict(self.env.cr.fetchall())
        for line in self:
            if line.id in paid:
                line.commission_amount = paid[line.id] or 0.0
            else:
                line.commission_amount = (
                    line.currency_id or self.env.company.currency_id
                ).round(line._commission_value())

    # ------------------------------------------------------------------
    def write(self, vals):
        """A settled line is frozen: it is what an agent was paid.

        The only writes still allowed on it are the ones that unwind the
        settlement itself -- ``state`` and ``settlement_id`` -- which is what
        cancelling a settlement does.
        """
        unwinding = set(vals) <= {'state', 'settlement_id', 'settlement_state'}
        if not unwinding:
            frozen = self.filtered(lambda line: line.state == 'settled')
            if frozen:
                raise UserError(self.env._(
                    "%(name)s belongs to settlement %(settlement)s and cannot be "
                    "changed. Cancel the settlement first.",
                    name=frozen[0].display_name,
                    settlement=frozen[0].settlement_id.name or '-'))
        return super().write(vals)

    def unlink(self):
        blocked = self.filtered(lambda line: line.state in ('confirmed', 'settled'))
        if blocked:
            raise UserError(self.env._(
                "%(count)s commission line(s) are already on a settlement. Cancel "
                "the settlement before deleting them.", count=len(blocked)))
        return super().unlink()

    def action_open_source(self):
        """Jump to whatever produced the line."""
        self.ensure_one()
        record = self.move_id or self.sale_order_id or self.payment_id
        if not record:
            raise UserError(self.env._(
                "This line has no source document left to open."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': record._name,
            'res_id': record.id,
            'view_mode': 'form',
        }
