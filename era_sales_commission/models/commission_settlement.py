from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .commission_agent_rate import LINE_COMMISSION_TYPES

MANAGER_GROUP = 'era_sales_commission.group_era_commission_manager'

#: The payslip input the commission is pushed as. Declared in
#: ``data/hr_payroll_data.xml`` together with the salary rule that reads it.
PAYSLIP_INPUT_XMLID = 'era_sales_commission.era_commission_input_type'


class EraCommissionSettlement(models.Model):
    """The document an agent is paid on.

    Approval is the gate: nothing leaves the company on a commission line until
    a Commission Manager signs this. Approving freezes the lines it covers --
    they stop being recomputed, and their amounts are what the printed statement
    shows for good.
    """

    _name = 'era.commission.settlement'
    _description = 'Commission Settlement'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True, default='/')
    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent', required=True, tracking=True,
        ondelete='restrict', index=True)
    partner_id = fields.Many2one(
        related='agent_id.partner_id', string='Payment Partner', store=True)
    employee_id = fields.Many2one(
        related='agent_id.employee_id', string='Employee', store=True)
    date_from = fields.Date(
        string='From', required=True, tracking=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(
        string='To', required=True, tracking=True,
        default=lambda self: fields.Date.end_of(
            fields.Date.context_today(self), 'month'))
    date_settlement = fields.Date(
        string='Settlement Date', default=fields.Date.context_today, required=True)
    plan_ids = fields.Many2many(
        'era.commission.plan', 'era_commission_settlement_plan_rel',
        'settlement_id', 'plan_id', string='Plans',
        help="Leave empty to settle every plan the agent is on.")
    line_ids = fields.One2many(
        'era.commission.line', 'settlement_id', string='Commission Lines')
    adjustment_ids = fields.One2many(
        'era.commission.adjustment', 'settlement_id', string='Adjustments')
    line_count = fields.Integer(
        string='# Lines', compute='_compute_amounts', store=True)

    amount_gross = fields.Monetary(
        string='Gross Commission', compute='_compute_amounts', store=True,
        currency_field='currency_id')
    amount_target_factor = fields.Float(
        string='Target Factor (%)', default=100.0, readonly=True, copy=False,
        digits='Discount',
        help="Read from the achievement tiers of the plan when the settlement is "
             "computed.")
    amount_target_adjust = fields.Monetary(
        string='Target Adjustment', compute='_compute_amounts', store=True,
        currency_field='currency_id')
    amount_adjustment = fields.Monetary(
        string='Bonuses and Deductions', compute='_compute_amounts', store=True,
        currency_field='currency_id')
    amount_total = fields.Monetary(
        string='Net Payable', compute='_compute_amounts', store=True,
        currency_field='currency_id', tracking=True)
    era_rep_total_commission = fields.Monetary(
        string='Total Commission of the Rep', compute='_compute_amounts',
        store=True, currency_field='currency_id',
        help="What the agent is actually owed, all commission types together, "
             "after the achievement factor and the bonuses and deductions. This "
             "is the figure pushed to a payslip.")
    commission_type_summary = fields.Text(
        string='By Commission Type', compute='_compute_type_summary',
        help="One line per commission type with its total, so a statement says "
             "where the money came from without reading every line.")

    era_payslip_id = fields.Many2one(
        'hr.payslip', string='Payslip', readonly=True, copy=False)
    era_payslip_input_id = fields.Many2one(
        'hr.payslip.input', string='Payslip Input', readonly=True, copy=False)
    era_payslip_pushed = fields.Boolean(
        string='Pushed to Payroll', readonly=True, copy=False)

    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('to_approve', 'To Approve'),
            ('approved', 'Approved'),
            ('posted', 'Posted'),
            ('paid', 'Paid'),
            ('cancel', 'Cancelled'),
        ],
        string='Status', default='draft', required=True, tracking=True, copy=False)
    payout_mode = fields.Selection(
        selection=[
            ('entry', 'Journal Entry'),
            ('bill', 'Vendor Bill'),
        ],
        string='Payout', required=True,
        default=lambda self: self.env.company.era_commission_payout_mode or 'entry')
    move_id = fields.Many2one(
        'account.move', string='Journal Entry', readonly=True, copy=False)
    payment_state = fields.Selection(
        related='move_id.payment_state', string='Payment Status')
    approved_by_id = fields.Many2one(
        'res.users', string='Approved by', readonly=True, copy=False)
    approved_date = fields.Datetime(string='Approved on', readonly=True, copy=False)
    note = fields.Html(string='Notes')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    @api.depends('line_ids.commission_amount', 'adjustment_ids.amount',
                 'adjustment_ids.type', 'amount_target_factor')
    def _compute_amounts(self):
        for settlement in self:
            gross = sum(settlement.line_ids.mapped('commission_amount'))
            adjustment = sum(
                (line.amount if line.type == 'bonus' else -line.amount)
                for line in settlement.adjustment_ids)
            factor = (settlement.amount_target_factor or 100.0) / 100.0
            settlement.amount_gross = gross
            settlement.line_count = len(settlement.line_ids)
            settlement.amount_target_adjust = gross * (factor - 1.0)
            settlement.amount_adjustment = adjustment
            settlement.amount_total = gross * factor + adjustment
            settlement.era_rep_total_commission = settlement.amount_total

    @api.depends('line_ids.commission_amount', 'line_ids.commission_type')
    def _compute_type_summary(self):
        labels = dict(LINE_COMMISSION_TYPES)
        for settlement in self:
            totals = {}
            for line in settlement.line_ids:
                totals.setdefault(line.commission_type, 0.0)
                totals[line.commission_type] += line.commission_amount
            settlement.commission_type_summary = '\n'.join(
                f'{labels.get(key, key)}: '
                f'{settlement.currency_id.round(value):,.2f}'
                for key, value in totals.items()) or False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'era.commission.settlement') or '/'
        return super().create(vals_list)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for settlement in self:
            if settlement.date_to < settlement.date_from:
                raise ValidationError(settlement.env._(
                    "%(name)s ends before it starts.", name=settlement.name))

    # ------------------------------------------------------------------
    # building the document
    # ------------------------------------------------------------------
    def _line_domain(self):
        """Lines of the period that are free to be settled."""
        self.ensure_one()
        domain = [
            ('agent_id', '=', self.agent_id.id),
            ('company_id', '=', self.company_id.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'draft'),
            ('settlement_id', '=', False),
        ]
        if self.plan_ids:
            domain.append(('plan_id', 'in', self.plan_ids.ids))
        return domain

    def _settlement_plans(self):
        self.ensure_one()
        if self.plan_ids:
            return self.plan_ids
        return self.agent_id._get_assignments(self.date_to).plan_id

    def action_compute(self):
        """Recompute the period, then take every free line into the document."""
        for settlement in self:
            if settlement.state not in ('draft', 'to_approve'):
                raise UserError(settlement.env._(
                    "%(name)s is %(state)s and can no longer be recomputed.",
                    name=settlement.name, state=settlement.state))
            settlement.line_ids.write({'settlement_id': False, 'state': 'draft'})
            self.env['era.commission.engine'].with_company(
                settlement.company_id
            ).generate(
                settlement.date_from, settlement.date_to,
                plans=settlement._settlement_plans(),
                agents=settlement.agent_id,
            )
            lines = self.env['era.commission.line'].search(settlement._line_domain())
            lines.write({'settlement_id': settlement.id, 'state': 'confirmed'})
            settlement.amount_target_factor = settlement._compute_target_factor() * 100.0
            settlement.message_post(body=settlement.env._(
                "Computed: %(count)s commission line(s).", count=len(lines)))
        return True

    def _compute_target_factor(self):
        """The lowest factor of the plans settled here.

        Several plans with targets on one statement is unusual; when it happens
        the agent is not paid twice on the same shortfall.
        """
        self.ensure_one()
        Target = self.env['era.commission.target']
        plans = self.line_ids.plan_id.filtered(
            lambda plan: plan.use_target and plan.target_mode == 'factor')
        factors = [
            Target._get_factor(self.agent_id, plan, self.date_from, self.date_to)
            for plan in plans
        ]
        return min(factors) if factors else 1.0

    def action_submit(self):
        for settlement in self:
            if not settlement.line_ids and not settlement.adjustment_ids:
                raise UserError(settlement.env._(
                    "%(name)s has nothing to pay. Compute it first.",
                    name=settlement.name))
            settlement.state = 'to_approve'

    def _check_manager(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(self.env._(
                "Only a Commission Manager may approve a settlement: approving it "
                "freezes the commission lines it covers and releases the money."))

    def action_approve(self):
        self._check_manager()
        for settlement in self:
            if settlement.state not in ('draft', 'to_approve'):
                raise UserError(settlement.env._(
                    "%(name)s is already approved.", name=settlement.name))
            if not settlement.line_ids and not settlement.adjustment_ids:
                raise UserError(settlement.env._(
                    "%(name)s has nothing to approve.", name=settlement.name))
            settlement.line_ids.write({'state': 'settled'})
            settlement.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })

    def action_refuse(self):
        self._check_manager()
        for settlement in self:
            settlement._release_lines()
            settlement.state = 'draft'
            settlement.message_post(body=settlement.env._(
                "Refused. The commission lines are free again."))

    # ------------------------------------------------------------------
    # the money
    # ------------------------------------------------------------------
    def _check_accounting_setup(self):
        self.ensure_one()
        company = self.company_id
        missing = []
        if self.payout_mode == 'entry':
            if not company.era_commission_journal_id:
                missing.append(self.env._('Commission Journal'))
            if not company.era_commission_expense_account_id:
                missing.append(self.env._('Commission Expense Account'))
            if not company.era_commission_payable_account_id:
                missing.append(self.env._('Commission Payable Account'))
        else:
            if not self.agent_id.partner_id:
                raise UserError(self.env._(
                    "%(agent)s has no payment partner, so no vendor bill can be "
                    "issued to them.", agent=self.agent_id.name))
            if not company.era_commission_expense_account_id:
                missing.append(self.env._('Commission Expense Account'))
        if missing:
            raise UserError(self.env._(
                "Set the following in Settings > Sales Commissions before posting "
                "%(name)s: %(missing)s.",
                name=self.name, missing=', '.join(missing)))

    def _bill_journal(self):
        self.ensure_one()
        journal = self.company_id.era_commission_journal_id
        if journal.type == 'purchase':
            return journal
        journal = self.env['account.journal'].search([
            ('type', '=', 'purchase'), ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not journal:
            raise UserError(self.env._(
                "No purchase journal is configured for %(company)s, so a vendor "
                "bill cannot be created.", company=self.company_id.name))
        return journal

    def _prepare_move_vals(self):
        self.ensure_one()
        company = self.company_id
        label = self.env._(
            'Commission %(name)s - %(agent)s',
            name=self.name, agent=self.agent_id.name)
        amount = self.amount_total
        if self.payout_mode == 'bill':
            return {
                'move_type': 'in_invoice',
                'partner_id': self.agent_id.partner_id.id,
                'journal_id': self._bill_journal().id,
                'invoice_date': self.date_settlement,
                'date': self.date_settlement,
                'ref': label,
                'company_id': company.id,
                'invoice_line_ids': [(0, 0, {
                    'name': label,
                    'quantity': 1.0,
                    'price_unit': amount,
                    'account_id': company.era_commission_expense_account_id.id,
                    'tax_ids': [(5, 0, 0)],
                })],
            }
        return {
            'move_type': 'entry',
            'journal_id': company.era_commission_journal_id.id,
            'date': self.date_settlement,
            'ref': label,
            'company_id': company.id,
            'partner_id': self.agent_id.partner_id.id or False,
            'line_ids': [
                (0, 0, {
                    'name': label,
                    'account_id': company.era_commission_expense_account_id.id,
                    'partner_id': self.agent_id.partner_id.id or False,
                    'debit': amount if amount > 0 else 0.0,
                    'credit': -amount if amount < 0 else 0.0,
                }),
                (0, 0, {
                    'name': label,
                    'account_id': company.era_commission_payable_account_id.id,
                    'partner_id': self.agent_id.partner_id.id or False,
                    'debit': -amount if amount < 0 else 0.0,
                    'credit': amount if amount > 0 else 0.0,
                }),
            ],
        }

    def action_post(self):
        for settlement in self:
            if settlement.state != 'approved':
                raise UserError(settlement.env._(
                    "%(name)s has to be approved before it is posted.",
                    name=settlement.name))
            if settlement.move_id:
                raise UserError(settlement.env._(
                    "%(name)s is already posted as %(move)s.",
                    name=settlement.name, move=settlement.move_id.name))
            if settlement.currency_id.is_zero(settlement.amount_total):
                raise UserError(settlement.env._(
                    "%(name)s totals zero, so there is nothing to post.",
                    name=settlement.name))
            settlement._check_accounting_setup()
            move = self.env['account.move'].create(settlement._prepare_move_vals())
            move.action_post()
            settlement.write({'move_id': move.id, 'state': 'posted'})
            settlement.message_post(body=settlement.env._(
                "Posted as %(move)s.", move=move.name))
        return True

    def action_register_payment(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(self.env._(
                "%(name)s has not been posted yet.", name=self.name))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Register Payment'),
            'res_model': 'account.payment.register',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'account.move',
                'active_ids': self.move_id.ids,
            },
        }

    def action_mark_paid(self):
        for settlement in self:
            if settlement.state != 'posted':
                raise UserError(settlement.env._(
                    "%(name)s has to be posted before it is marked as paid.",
                    name=settlement.name))
            settlement.state = 'paid'

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
        }

    # ------------------------------------------------------------------
    # undoing it
    # ------------------------------------------------------------------
    def _release_lines(self):
        for settlement in self:
            settlement.line_ids.write({'state': 'draft'})
            settlement.line_ids.write({'settlement_id': False})

    # ------------------------------------------------------------------
    # payroll
    # ------------------------------------------------------------------
    def _payslip_domain(self):
        """A draft payslip of the employee whose period overlaps the statement."""
        self.ensure_one()
        return [
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'draft'),
            ('company_id', '=', self.company_id.id),
            ('date_from', '<=', self.date_to),
            ('date_to', '>=', self.date_from),
        ]

    def action_push_to_payslip(self):
        """Carry the net commission onto an open payslip as an other input.

        A button, never a hook on ``action_post``: paying an agent through the
        payroll and paying them through the accounts are two different decisions
        and a company makes only one of them. Pushing twice is refused rather
        than doubled -- a payslip has no way of telling two identical inputs
        apart.
        """
        input_type = self.env.ref(PAYSLIP_INPUT_XMLID)
        for settlement in self:
            if settlement.state not in ('approved', 'posted', 'paid'):
                raise UserError(settlement.env._(
                    "%(name)s has to be approved before it goes to payroll.",
                    name=settlement.name))
            if settlement.era_payslip_pushed:
                raise UserError(settlement.env._(
                    "%(name)s was already pushed to payslip %(slip)s.",
                    name=settlement.name,
                    slip=settlement.era_payslip_id.display_name or '-'))
            if not settlement.employee_id:
                raise UserError(settlement.env._(
                    "%(agent)s is not linked to an employee, so there is no "
                    "payslip to push %(name)s onto. Set the employee on the "
                    "agent first.",
                    agent=settlement.agent_id.name, name=settlement.name))
            payslip = self.env['hr.payslip'].search(
                settlement._payslip_domain(), order='date_from desc', limit=1)
            if not payslip:
                raise UserError(settlement.env._(
                    "%(employee)s has no draft payslip covering %(from)s to "
                    "%(to)s. Create the payslip first, then push.",
                    employee=settlement.employee_id.name,
                    **{'from': settlement.date_from, 'to': settlement.date_to}))
            payslip_input = self.env['hr.payslip.input'].create({
                'payslip_id': payslip.id,
                'input_type_id': input_type.id,
                'name': settlement.env._(
                    'Commission %(name)s', name=settlement.name),
                'amount': settlement.era_rep_total_commission,
            })
            settlement.write({
                'era_payslip_id': payslip.id,
                'era_payslip_input_id': payslip_input.id,
                'era_payslip_pushed': True,
            })
            settlement.message_post(body=settlement.env._(
                "Pushed %(amount)s to payslip %(slip)s.",
                amount=settlement.era_rep_total_commission,
                slip=payslip.display_name))
        return True

    def action_view_payslip(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.payslip',
            'res_id': self.era_payslip_id.id,
            'view_mode': 'form',
        }

    def action_cancel(self):
        for settlement in self:
            if settlement.era_payslip_pushed:
                raise UserError(settlement.env._(
                    "%(name)s has been pushed to payslip %(slip)s. Remove the "
                    "commission input from the payslip before cancelling it, "
                    "otherwise the agent is paid for a statement that no longer "
                    "exists.",
                    name=settlement.name,
                    slip=settlement.era_payslip_id.display_name or '-'))
            if settlement.state == 'paid':
                raise UserError(settlement.env._(
                    "%(name)s has been paid and cannot be cancelled.",
                    name=settlement.name))
            if settlement.move_id and settlement.move_id.state == 'posted':
                raise UserError(settlement.env._(
                    "Reverse or reset journal entry %(move)s before cancelling "
                    "%(name)s.", move=settlement.move_id.name, name=settlement.name))
            settlement._release_lines()
            settlement.state = 'cancel'

    def action_draft(self):
        for settlement in self:
            if settlement.state not in ('cancel', 'to_approve'):
                raise UserError(settlement.env._(
                    "%(name)s cannot go back to draft from %(state)s.",
                    name=settlement.name, state=settlement.state))
            settlement._release_lines()
            settlement.state = 'draft'

    def unlink(self):
        """A frozen line must never be left behind without its document."""
        blocked = self.filtered(
            lambda settlement: settlement.state in ('approved', 'posted', 'paid'))
        if blocked:
            raise UserError(self.env._(
                "%(name)s is %(state)s. Cancel it first: deleting it would leave "
                "its commission lines settled and unpayable.",
                name=blocked[0].name, state=blocked[0].state))
        self._release_lines()
        return super().unlink()

    def _report_groups(self):
        """The statement's lines, grouped by the document that produced them.

        Built here rather than in QWeb: a template that has to carry a running
        variable across a loop is the kind of thing that silently prints the
        wrong subtotal.
        """
        self.ensure_one()
        groups = {}
        for line in self.line_ids.sorted(key=lambda line: (line.date, line.id)):
            document = line.move_id or line.sale_order_id
            if document:
                key = f'{document._name},{document.id}'
                label = document.name
            else:
                key = f'other,{line.line_type}'
                label = dict(
                    self.env['era.commission.line']._fields['line_type'].selection
                )[line.line_type]
            group = groups.setdefault(key, {
                'label': label,
                'date': line.date,
                'partner': line.partner_id.display_name or '-',
                'lines': self.env['era.commission.line'].browse(),
                'amount': 0.0,
                'refund': 0.0,
                'tax': 0.0,
                'target': 0.0,
            })
            group['lines'] |= line
            group['amount'] += line.commission_amount
            group['refund'] += line.era_refund_amount
            group['tax'] += line.tax_deducted
            group['target'] += line.target_amount
        return list(groups.values())

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'era_sales_commission.action_report_commission_statement'
        ).report_action(self)


class EraCommissionAdjustment(models.Model):
    """A bonus or a deduction a human decided on, next to what the rules gave."""

    _name = 'era.commission.adjustment'
    _description = 'Commission Adjustment'
    _order = 'settlement_id, id'

    settlement_id = fields.Many2one(
        'era.commission.settlement', string='Settlement', required=True,
        ondelete='cascade', index=True)
    name = fields.Char(string='Reason', required=True)
    type = fields.Selection(
        selection=[
            ('bonus', 'Bonus'),
            ('deduction', 'Deduction'),
        ],
        string='Type', default='bonus', required=True)
    amount = fields.Monetary(
        string='Amount', required=True, currency_field='currency_id')
    user_id = fields.Many2one(
        'res.users', string='Decided by', default=lambda self: self.env.user)
    company_id = fields.Many2one(related='settlement_id.company_id', store=True)
    currency_id = fields.Many2one(related='settlement_id.currency_id')

    @api.constrains('amount')
    def _check_amount(self):
        for adjustment in self:
            if adjustment.amount < 0:
                raise ValidationError(adjustment.env._(
                    "Enter a positive amount and pick Deduction to take it off."))
