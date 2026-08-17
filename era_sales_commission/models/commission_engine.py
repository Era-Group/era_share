from collections import defaultdict
from datetime import datetime, time

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

#: Invoice types a sales commission can ever be earned on.
CUSTOMER_MOVE_TYPES = ('out_invoice', 'out_refund')

#: The bases that are counted by the unit rather than by the amount.
QUANTITY_BASES = ('qty_sold', 'qty_collected')


class EraCommissionEngine(models.AbstractModel):
    """The only place commission lines are created.

    Computation is a batch: a cron, a button, or building a settlement. It is
    deliberately not hooked into ``action_confirm`` or ``_post``. A bad rule
    must never be able to block the posting of an invoice, and every line has to
    stay reproducible from the source documents alone.

    Re-running it is safe. Each line carries an ``origin_key`` built from its
    source, so a second run updates the draft lines it already produced instead
    of doubling them, and never touches a line that has been confirmed or paid.

    The engine writes what a line was earned on -- base, tax, quantity, rate,
    unit price. It never writes the commission itself: that is computed on the
    line, so correcting a percentage by hand is enough to correct the money.
    """

    _name = 'era.commission.engine'
    _description = 'Commission Computation Engine'

    # ------------------------------------------------------------------
    # entry points
    # ------------------------------------------------------------------
    @api.model
    def generate(self, date_from, date_to, plans=None, agents=None, commit=True,
                 options=None):
        """Compute every commission earned between the two dates.

        :param plans: restrict to these ``era.commission.plan`` records.
        :param agents: restrict to these ``era.commission.agent`` records.
        :param commit: when ``False`` nothing is written and the values that
            *would* be created are returned instead -- that is the simulation.
        :param options: ``deduct_tax`` / ``tax_method`` / ``use_target``, as the
            generation wizard asked for them. They override the plan.
        :return: the commission lines touched, or the list of values.
        """
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)
        plans = plans if plans is not None else self._plans_in_scope(date_from, date_to)
        plans = plans.filtered(lambda plan: plan.state == 'approved')

        vals_list = []
        for plan in plans:
            opts = self._plan_options(plan, options)
            for occurrence in self._collect_occurrences(
                    plan, opts, date_from, date_to, agents):
                vals = self._apply_rate(plan, opts, occurrence)
                if vals:
                    vals_list.append(vals)
        if not commit:
            return vals_list

        self._reverse_stale_lines()
        lines = self._create_lines(vals_list)
        self._allocate_targets(lines, date_from, date_to, options)
        lines |= self._generate_overrides(lines)
        return lines

    @api.model
    def _plan_options(self, plan, options=None):
        """What the plan says, unless the caller said otherwise."""
        opts = {
            'deduct_tax': plan.deduct_tax,
            'tax_method': plan.tax_method or 'actual',
            'use_target': plan.use_target and plan.target_mode == 'deduct',
        }
        for key in tuple(opts):
            if options and options.get(key) is not None:
                opts[key] = options[key]
        if plan.basis in QUANTITY_BASES:
            # a unit price is not an amount: there is no tax inside it
            opts['deduct_tax'] = False
        return opts

    @api.model
    def _cron_generate(self):
        """Daily recomputation of the current and the previous month."""
        today = fields.Date.context_today(self)
        date_from = (today - relativedelta(months=1)).replace(day=1)
        date_to = fields.Date.end_of(today, 'month')
        for company in self.env['res.company'].search([]):
            if not company.era_commission_auto_generate:
                continue
            self.with_company(company).generate(date_from, date_to)
        return True

    @api.model
    def _plans_in_scope(self, date_from, date_to):
        return self.env['era.commission.plan'].search([
            ('state', '=', 'approved'),
            ('company_id', 'in', self.env.companies.ids),
            '|', ('date_from', '=', False), ('date_from', '<=', date_to),
            '|', ('date_to', '=', False), ('date_to', '>=', date_from),
        ])

    # ------------------------------------------------------------------
    # who earns on a document
    # ------------------------------------------------------------------
    @api.model
    def _document_shares(self, record):
        """[(agent, share ratio)] as written on a sales order or an invoice."""
        splits = record.era_agent_share_ids.filtered('agent_id')
        if splits:
            return [(split.agent_id, split.share / 100.0) for split in splits]
        if record.era_agent_id:
            return [(record.era_agent_id, 1.0)]
        return []

    @api.model
    def _agents_for_document(self, record, plan, date, agents=None, shares=None):
        """[(agent, share ratio)] of the agents this plan pays on that document.

        The share written on the document is multiplied by the share of the
        agent's assignment to the plan, so both splits compose.
        """
        result = []
        for agent, doc_share in (shares if shares is not None
                                 else self._document_shares(record)):
            if agents is not None and agent not in agents:
                continue
            if not agent._is_active_on(date):
                continue
            for assignment in agent._get_assignments(date, plan=plan):
                share = doc_share * assignment.share / 100.0
                if share:
                    result.append((agent, share))
        return result

    # ------------------------------------------------------------------
    # collecting what happened
    # ------------------------------------------------------------------
    @api.model
    def _collect_occurrences(self, plan, opts, date_from, date_to, agents=None):
        if plan.basis == 'order':
            return self._collect_orders(plan, opts, date_from, date_to, agents)
        if plan.basis == 'collection':
            return self._collect_collections(plan, opts, date_from, date_to, agents)
        if plan.basis in QUANTITY_BASES:
            return self._collect_quantities(plan, opts, date_from, date_to, agents)
        return self._collect_invoices(plan, opts, date_from, date_to, agents)

    @api.model
    def _convert(self, amount, currency, company, date):
        if not currency or currency == company.currency_id:
            return amount
        return currency._convert(amount, company.currency_id, company, date)

    @api.model
    def _is_excluded_product(self, plan, product):
        return bool(product) and product in plan.excluded_product_ids

    # ------------------------------------------------------------------
    # the tax
    # ------------------------------------------------------------------
    @api.model
    def _tax_figures(self, plan, opts, currency, date, price_subtotal, price_total):
        """``(gross, tax)`` in company currency, both unsigned.

        With the tax deducted the base is collected **with** the tax in it and
        the tax is kept beside it, so ``base - tax`` is literally the net the
        rate applies to. Without it the plan's own base is used and nothing is
        taken off -- taking the tax off an untaxed base would deduct it twice.
        """
        company = plan.company_id
        subtotal = self._convert(price_subtotal, currency, company, date)
        total = self._convert(price_total, currency, company, date)
        if not opts['deduct_tax']:
            gross = total if plan.amount_base == 'total' else subtotal
            return gross, 0.0
        if opts['tax_method'] == 'divide':
            rate = company.era_commission_tax_rate or 0.0
            tax = total - total / (1.0 + rate / 100.0) if rate else 0.0
            return total, tax
        return total, total - subtotal

    # ------------------------------------------------------------------
    @api.model
    def _collect_orders(self, plan, opts, date_from, date_to, agents=None):
        orders = self.env['sale.order'].search([
            ('state', '=', 'sale'),
            ('company_id', '=', plan.company_id.id),
            ('date_order', '>=', datetime.combine(date_from, time.min)),
            ('date_order', '<=', datetime.combine(date_to, time.max)),
        ])
        occurrences = []
        for order in orders:
            date = order.date_order.date()
            shares = self._agents_for_document(order, plan, date, agents)
            if not shares:
                continue
            for line in order.order_line:
                if line.display_type:
                    continue
                if plan.exclude_downpayment and line.is_downpayment:
                    continue
                if self._is_excluded_product(plan, line.product_id):
                    continue
                gross, tax = self._tax_figures(
                    plan, opts, order.currency_id, date,
                    line.price_subtotal, line.price_total)
                untaxed = self._convert(
                    line.price_subtotal, order.currency_id, plan.company_id, date)
                cost = line.product_uom_qty * line.product_id.standard_price
                for agent, share in shares:
                    occurrences.append({
                        'plan': plan,
                        'agent': agent,
                        'share': share,
                        'date': date,
                        'name': f'{order.name} - {line.product_id.display_name or line.name}',
                        'partner': order.partner_id,
                        'product': line.product_id,
                        'team': order.team_id,
                        'quantity': line.product_uom_qty * share,
                        'base_amount': gross * share,
                        'tax': tax * share,
                        'margin': (untaxed - cost) * share,
                        'company': plan.company_id,
                        'source_ref': f'sale.order.line:{line.id}',
                        'values': {
                            'sale_order_id': order.id,
                            'sale_line_id': line.id,
                        },
                    })
        return occurrences

    @api.model
    def _invoice_domain(self, plan, date_from, date_to):
        move_types = list(CUSTOMER_MOVE_TYPES)
        if not plan.deduct_refunds:
            move_types.remove('out_refund')
        return [
            ('move_type', 'in', move_types),
            ('state', '=', 'posted'),
            ('company_id', '=', plan.company_id.id),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ]

    @api.model
    def _invoice_line_figures(self, plan, opts, move, line, date):
        """``(gross, tax, untaxed, cost)`` of an invoice line, company currency.

        A credit note is returned negative: what the sale paid, the return takes
        back.
        """
        sign = -1.0 if move.move_type == 'out_refund' else 1.0
        gross, tax = self._tax_figures(
            plan, opts, move.currency_id, date, line.price_subtotal,
            line.price_total)
        untaxed = self._convert(
            line.price_subtotal, move.currency_id, plan.company_id, date)
        cost = line.quantity * line.product_id.standard_price
        return sign * gross, sign * tax, sign * untaxed, sign * cost

    @api.model
    def _invoice_product_lines(self, plan, move):
        lines = move.line_ids.filtered(lambda line: line.display_type == 'product')
        if plan.exclude_downpayment:
            lines = lines.filtered(
                lambda line: not any(line.sale_line_ids.mapped('is_downpayment')))
        return lines.filtered(
            lambda line: not self._is_excluded_product(plan, line.product_id))

    @api.model
    def _collect_invoices(self, plan, opts, date_from, date_to, agents=None):
        moves = self.env['account.move'].search(
            self._invoice_domain(plan, date_from, date_to))
        occurrences = []
        for move in moves:
            date = move.invoice_date
            shares = self._agents_for_document(move, plan, date, agents)
            if not shares:
                continue
            for line in self._invoice_product_lines(plan, move):
                gross, tax, untaxed, cost = self._invoice_line_figures(
                    plan, opts, move, line, date)
                sign = -1.0 if move.move_type == 'out_refund' else 1.0
                for agent, share in shares:
                    occurrences.append({
                        'plan': plan,
                        'agent': agent,
                        'share': share,
                        'date': date,
                        'name': f'{move.name} - {line.product_id.display_name or line.name}',
                        'partner': move.partner_id,
                        'product': line.product_id,
                        'team': move.team_id,
                        'quantity': sign * line.quantity * share,
                        'base_amount': gross * share,
                        'tax': tax * share,
                        'refund_amount': gross * share if sign < 0 else 0.0,
                        'margin': (untaxed - cost) * share,
                        'company': plan.company_id,
                        'source_ref': f'account.move.line:{line.id}',
                        'values': {
                            'move_id': move.id,
                            'move_line_id': line.id,
                            'sale_line_id': line.sale_line_ids[:1].id,
                            'sale_order_id': line.sale_line_ids[:1].order_id.id,
                        },
                    })
        return occurrences

    @api.model
    def _collection_partials(self, plan, date_from, date_to):
        """Matchings that brought money in, with the invoice they paid.

        Only a matching whose other side is *not* a customer invoice counts. A
        credit note reconciled against an invoice moves no cash, and counting
        both sides of it would pay the commission twice.
        """
        partials = self.env['account.partial.reconcile'].search([
            ('company_id', '=', plan.company_id.id),
            ('max_date', '>=', date_from),
            ('max_date', '<=', date_to),
        ])
        result = []
        for partial in partials:
            sides = (
                (partial.debit_move_id, partial.credit_move_id),
                (partial.credit_move_id, partial.debit_move_id),
            )
            invoice_side = counterpart = None
            for line, other in sides:
                if line.move_id.move_type in CUSTOMER_MOVE_TYPES:
                    if other.move_id.move_type in CUSTOMER_MOVE_TYPES:
                        invoice_side = None
                        break
                    invoice_side, counterpart = line, other
                    break
            if not invoice_side:
                continue
            move = invoice_side.move_id
            if move.state != 'posted':
                continue
            if move.move_type == 'out_refund' and not plan.deduct_refunds:
                continue
            total = abs(move.amount_total_signed)
            if not total:
                continue
            result.append((partial, move, min(partial.amount / total, 1.0), counterpart))
        return result

    @api.model
    def _collect_collections(self, plan, opts, date_from, date_to, agents=None):
        occurrences = []
        for partial, move, ratio, counterpart in self._collection_partials(
                plan, date_from, date_to):
            date = partial.max_date
            payment = counterpart.move_id.origin_payment_id
            # a rep who collects someone else's invoice is the one credited
            document_shares = None
            if payment and payment.era_agent_id:
                document_shares = [(payment.era_agent_id, 1.0)]
            shares = self._agents_for_document(
                move, plan, date, agents, shares=document_shares)
            if not shares:
                continue
            for line in self._invoice_product_lines(plan, move):
                gross, tax, untaxed, cost = self._invoice_line_figures(
                    plan, opts, move, line, move.invoice_date or date)
                sign = -1.0 if move.move_type == 'out_refund' else 1.0
                for agent, share in shares:
                    portion = share * ratio
                    occurrences.append({
                        'plan': plan,
                        'agent': agent,
                        'share': share,
                        'date': date,
                        'name': f'{move.name} - {line.product_id.display_name or line.name}',
                        'partner': move.partner_id,
                        'product': line.product_id,
                        'team': move.team_id,
                        'quantity': sign * line.quantity * portion,
                        'base_amount': gross * portion,
                        'tax': tax * portion,
                        'refund_amount': gross * portion if sign < 0 else 0.0,
                        'collection_ratio': ratio * 100.0,
                        'margin': (untaxed - cost) * portion,
                        'company': plan.company_id,
                        'source_ref': (
                            f'account.partial.reconcile:{partial.id}'
                            f'/account.move.line:{line.id}'),
                        'values': {
                            'move_id': move.id,
                            'move_line_id': line.id,
                            'partial_id': partial.id,
                            'payment_id': payment.id,
                            'sale_line_id': line.sale_line_ids[:1].id,
                            'sale_order_id': line.sale_line_ids[:1].order_id.id,
                        },
                    })
        return occurrences

    # ------------------------------------------------------------------
    # quantity commissions
    # ------------------------------------------------------------------
    @api.model
    def _move_collection_ratio(self, move):
        """How much of an invoice the customer has actually paid, in [0, 1].

        An estimate read off the residual of the whole invoice, not a follow-up
        of one product inside a payment: money is paid against a document, not
        against a line of it.
        """
        total = abs(move.amount_total_signed)
        if not total:
            return 0.0
        residual = abs(move.amount_residual_signed)
        return min(max((total - residual) / total, 0.0), 1.0)

    @api.model
    def _collect_quantities(self, plan, opts, date_from, date_to, agents=None):
        """One occurrence per agent, product and period -- not per invoice line.

        The unit price is chosen on the **net** quantity of the product over the
        period, and that quantity is only known once the period is added up. A
        product whose returns outweigh its sales counts as zero rather than
        turning into a negative commission the rep never earned.
        """
        collected = plan.basis == 'qty_collected'
        moves = self.env['account.move'].search(
            self._invoice_domain(plan, date_from, date_to))
        buckets = defaultdict(lambda: {
            'qty_sold': 0.0, 'qty_returned': 0.0,
            'qty_sold_paid': 0.0, 'qty_returned_paid': 0.0,
            'base': 0.0, 'refund': 0.0, 'agent': None, 'product': None,
            'partner': None, 'team': None,
        })
        for move in moves:
            date = move.invoice_date
            shares = self._agents_for_document(move, plan, date, agents)
            if not shares:
                continue
            ratio = self._move_collection_ratio(move) if collected else 1.0
            refund = move.move_type == 'out_refund'
            for line in self._invoice_product_lines(plan, move):
                if not line.product_id:
                    continue
                gross, _tax, _untaxed, _cost = self._invoice_line_figures(
                    plan, opts, move, line, date)
                for agent, share in shares:
                    bucket = buckets[(agent.id, line.product_id.id)]
                    bucket['agent'] = agent
                    bucket['product'] = line.product_id
                    bucket['partner'] = bucket['partner'] or move.partner_id
                    bucket['team'] = bucket['team'] or move.team_id
                    quantity = line.quantity * share
                    if refund:
                        bucket['qty_returned'] += quantity
                        bucket['qty_returned_paid'] += quantity * ratio
                        bucket['refund'] += gross * share
                    else:
                        bucket['qty_sold'] += quantity
                        bucket['qty_sold_paid'] += quantity * ratio
                    bucket['base'] += gross * share

        AgentRate = self.env['era.commission.agent.rate']
        Tier = self.env['era.commission.unit.price.tier']
        occurrences = []
        for (agent_id, product_id), bucket in buckets.items():
            agent, product = bucket['agent'], bucket['product']
            net_sold = max(bucket['qty_sold'] - bucket['qty_returned'], 0.0)
            net_paid = max(
                bucket['qty_sold_paid'] - bucket['qty_returned_paid'], 0.0)
            quantity = net_paid if collected else net_sold
            unit_price = Tier._price_for(product, quantity)
            if not unit_price:
                _rate, unit_price = AgentRate._rate_for(
                    agent, plan._commission_type(), product)
            if not unit_price:
                unit_price = product.commission_rate_per_unit
            occurrences.append({
                'plan': plan,
                'agent': agent,
                'share': 100.0 / 100.0,
                'date': date_to,
                'date_from': date_from,
                'date_to': date_to,
                'name': self.env._(
                    '%(product)s - %(start)s to %(end)s',
                    product=product.display_name, start=date_from, end=date_to),
                'partner': bucket['partner'] or self.env['res.partner'],
                'product': product,
                'team': bucket['team'] or self.env['crm.team'],
                'quantity': quantity,
                'base_amount': bucket['base'],
                'tax': 0.0,
                'refund_amount': bucket['refund'],
                'collection_ratio': (
                    (net_paid / net_sold * 100.0) if collected and net_sold else 0.0),
                'unit_price': unit_price,
                'margin': 0.0,
                'company': plan.company_id,
                'source_ref': (
                    f'product:{product_id}|{date_from}:{date_to}'),
                'values': {},
            })
        return occurrences

    # ------------------------------------------------------------------
    # turning an occurrence into a line
    # ------------------------------------------------------------------
    @api.model
    def _origin_key(self, plan, rule, line_type, source_ref, agent):
        return f'{plan.id}|{rule.id}|{line_type}|{source_ref}|{agent.id}'

    @api.model
    def _matching_rule(self, plan, occurrence):
        """First matching rule wins; nothing is stacked on one line."""
        for rule in plan.rule_ids.sorted(key=lambda rule: (rule.sequence, rule.id)):
            if rule._match(occurrence):
                return rule
        return self.env['era.commission.rule']

    @api.model
    def _apply_rate(self, plan, opts, occurrence):
        """Build the values of one commission line.

        The percentage of the agent wins over the percentage of a rule -- that
        is the whole point of putting it on the agent. A rule that computes on
        tiers, on a fixed amount or under a cap keeps the last word, because
        there is no single percentage that could stand in for it.
        """
        agent = occurrence['agent']
        commission_type = plan._commission_type()
        currency = plan.company_id.currency_id
        quantity = occurrence['quantity']

        rule = self._matching_rule(plan, occurrence)
        if plan.rule_ids and not rule:
            return None

        agent_rate, agent_unit_price = self.env['era.commission.agent.rate']._rate_for(
            agent, commission_type, occurrence.get('product'))

        if commission_type in QUANTITY_BASES:
            # nothing left once the returns are taken off: no line at all,
            # rather than a zero on every product of the catalogue
            if not quantity:
                return None
            unit_price = occurrence.get('unit_price') or agent_unit_price
            rate = 0.0
        else:
            unit_price = 0.0
            base = rule._rule_base(occurrence) if rule else occurrence['base_amount']
            if rule and rule.calc_type != 'percent':
                amount = rule._compute_amount(base, quantity)
                if not amount:
                    return None
                rate = rule._effective_rate(base, amount)
            elif agent_rate:
                rate = agent_rate
            elif rule:
                if not rule._compute_amount(base, quantity):
                    return None
                rate = rule.rate
            else:
                rate = 0.0

        return {
            'name': occurrence['name'],
            'date': occurrence['date'],
            'date_from': occurrence.get('date_from', False),
            'date_to': occurrence.get('date_to', False),
            'agent_id': agent.id,
            'plan_id': plan.id,
            'rule_id': rule.id,
            'commission_type': commission_type,
            'line_type': 'sale',
            'partner_id': occurrence['partner'].id,
            'product_id': occurrence['product'].id,
            'team_id': occurrence['team'].id if occurrence['team'] else False,
            'quantity': quantity,
            'base_amount': currency.round(occurrence['base_amount']),
            'margin_amount': currency.round(occurrence.get('margin', 0.0)),
            'tax_deducted': currency.round(occurrence.get('tax', 0.0)),
            'deduct_tax': opts['deduct_tax'],
            'tax_method': opts['tax_method'],
            'era_refund_amount': currency.round(occurrence.get('refund_amount', 0.0)),
            'era_collection_ratio': occurrence.get('collection_ratio', 0.0),
            'use_target': False,
            'target_amount': 0.0,
            'target_qty': 0.0,
            'share_rate': occurrence['share'] * 100.0,
            'rate': rate,
            'unit_price': unit_price,
            'state': 'draft',
            'company_id': plan.company_id.id,
            'origin_key': self._origin_key(
                plan, rule, 'sale', occurrence['source_ref'], agent),
            **occurrence['values'],
        }

    @api.model
    def _create_lines(self, vals_list):
        """Write the values, updating draft lines and never frozen ones."""
        Line = self.env['era.commission.line']
        if not vals_list:
            return Line.browse()
        keys = [vals['origin_key'] for vals in vals_list]
        existing = Line.with_context(active_test=False).search([
            ('origin_key', 'in', keys),
            ('company_id', 'in', list({vals['company_id'] for vals in vals_list})),
        ])
        by_key = {line.origin_key: line for line in existing}
        result = Line.browse()
        to_create = []
        for vals in vals_list:
            line = by_key.get(vals['origin_key'])
            if line is None:
                to_create.append(vals)
            elif line.state == 'draft':
                line.write({
                    key: value for key, value in vals.items()
                    if key not in ('origin_key', 'company_id', 'state')
                })
                result |= line
            else:
                # confirmed or settled: what was paid stays what was paid.
                result |= line
        if to_create:
            result |= Line.create(to_create)
        return result

    # ------------------------------------------------------------------
    # the target, spread over the period
    # ------------------------------------------------------------------
    @api.model
    def _allocate_targets(self, lines, date_from, date_to, options=None):
        """Give every line of the period its share of the agent's target.

        ``target x base(line) / sum(base)`` rather than one deduction line, so
        the formula holds line by line and the total of the statement is exactly
        ``(sum of the base - the target) x rate``, while every document is still
        traceable on its own.
        """
        Target = self.env['era.commission.target']
        draft = lines.filtered(
            lambda line: line.state == 'draft' and line.line_type == 'sale')
        if not draft:
            return
        groups = defaultdict(lambda: self.env['era.commission.line'])
        for line in draft:
            groups[(line.agent_id, line.plan_id, line.commission_type)] |= line

        for (agent, plan, commission_type), group in groups.items():
            opts = self._plan_options(plan, options) if plan else {'use_target': False}
            if not opts['use_target']:
                group.write({'use_target': False, 'target_amount': 0.0,
                             'target_qty': 0.0})
                continue
            target = Target._target_for(
                agent, commission_type, date_from, date_to, plan=plan)
            if not target:
                group.write({'use_target': False, 'target_amount': 0.0,
                             'target_qty': 0.0})
                continue
            on_quantity = commission_type in QUANTITY_BASES
            field = 'quantity' if on_quantity else 'base_amount'
            expected = target.target_qty if on_quantity else target.target_amount
            total = sum(group.mapped(field))
            if not total or not expected:
                group.write({'use_target': False, 'target_amount': 0.0,
                             'target_qty': 0.0})
                continue
            for line in group:
                share = expected * line[field] / total
                line.write({
                    'use_target': True,
                    'target_amount': 0.0 if on_quantity else share,
                    'target_qty': share if on_quantity else 0.0,
                })

    # ------------------------------------------------------------------
    # the manager's cut
    # ------------------------------------------------------------------
    @api.model
    def _generate_overrides(self, lines):
        """Pay every manager above an agent on what their team sold.

        Each level is computed from the original line, not from the override
        below it, so a deep hierarchy does not compound into a number nobody
        can explain. The override is a derived amount, not a percentage of a
        document, so it is written as a manual amount on an adjustment line.
        """
        vals_list = []
        for line in lines.filtered(
                lambda line: line.line_type == 'sale' and line.state == 'draft'):
            manager = line.agent_id.parent_id
            seen = line.agent_id
            while manager and manager not in seen:
                seen |= manager
                if manager.override_rate and manager._is_active_on(line.date):
                    base = line.base_amount if manager.override_basis == 'base' \
                        else line.commission_amount
                    amount = base * manager.override_rate / 100.0
                    if amount:
                        vals_list.append({
                            'name': self.env._(
                                'Override on %(agent)s - %(name)s',
                                agent=line.agent_id.name, name=line.name or ''),
                            'date': line.date,
                            'agent_id': manager.id,
                            'plan_id': line.plan_id.id,
                            'commission_type': 'adjustment',
                            'line_type': 'override',
                            'parent_line_id': line.id,
                            'partner_id': line.partner_id.id,
                            'product_id': line.product_id.id,
                            'team_id': line.team_id.id,
                            'sale_order_id': line.sale_order_id.id,
                            'sale_line_id': line.sale_line_id.id,
                            'move_id': line.move_id.id,
                            'move_line_id': line.move_line_id.id,
                            'partial_id': line.partial_id.id,
                            'quantity': 0.0,
                            'base_amount': line.company_id.currency_id.round(base),
                            'share_rate': 100.0,
                            'rate': manager.override_rate,
                            'manual_amount': line.company_id.currency_id.round(amount),
                            'state': 'draft',
                            'company_id': line.company_id.id,
                            'origin_key': f'{line.origin_key}|ov|{manager.id}',
                        })
                manager = manager.parent_id
        return self._create_lines(vals_list)

    # ------------------------------------------------------------------
    # claw-backs
    # ------------------------------------------------------------------
    @api.model
    def _reverse_stale_lines(self):
        """Give back what was paid on a document that no longer stands.

        A settled line is never edited: it is what an agent was paid and what a
        printed statement shows. When its source is cancelled, or a collection
        is un-reconciled, a negative ``reversal`` line is written on today's
        date instead. It lands on the next settlement, which is what a
        claw-back is.
        """
        Line = self.env['era.commission.line']
        candidates = Line.search([
            ('state', 'in', ('confirmed', 'settled')),
            ('line_type', 'in', ('sale', 'override')),
            ('company_id', 'in', self.env.companies.ids),
        ])
        stale = candidates.filtered(self._is_stale)
        if not stale:
            return Line.browse()
        already = set(Line.search([
            ('line_type', '=', 'reversal'),
            ('reversed_line_id', 'in', stale.ids),
        ]).mapped('reversed_line_id.id'))
        today = fields.Date.context_today(self)
        vals_list = [{
            'name': self.env._('Reversal of %(name)s', name=line.name or line.id),
            'date': today,
            'agent_id': line.agent_id.id,
            'plan_id': line.plan_id.id,
            'commission_type': 'adjustment',
            'line_type': 'reversal',
            'reversed_line_id': line.id,
            'partner_id': line.partner_id.id,
            'product_id': line.product_id.id,
            'team_id': line.team_id.id,
            'base_amount': -line.base_amount,
            'era_refund_amount': -line.base_amount,
            'share_rate': line.share_rate,
            'rate': line.rate,
            'manual_amount': -line.commission_amount,
            'state': 'draft',
            'company_id': line.company_id.id,
            'origin_key': f'reversal|{line.id}',
        } for line in stale if line.id not in already]
        return self._create_lines(vals_list)

    @api.model
    def _is_stale(self, line):
        """Has the document behind a paid line gone away."""
        if line.line_type == 'override' and line.parent_line_id:
            return self._is_stale(line.parent_line_id)
        if line.sale_order_id and line.sale_order_id.state == 'cancel':
            return True
        if line.move_id and line.move_id.state == 'cancel':
            return True
        if line.plan_id.basis == 'collection' and line.line_type == 'sale' \
                and not line.partial_id:
            # the matching that made the money real has been undone
            return True
        return False
