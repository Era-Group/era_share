from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

class LegalEngagement(models.Model):
    _name='legal.engagement'; _description='Legal Engagement'; _inherit=['mail.thread']; _check_company_auto=True
    name=fields.Char(required=True); case_id=fields.Many2one('legal.case',required=True,check_company=True); company_id=fields.Many2one(related='case_id.company_id',store=True,index=True)
    billing_type=fields.Selection([('fixed','Fixed'),('hourly','Hourly'),('milestone','Milestone'),('success','Success Fee')],required=True)
    hourly_rate=fields.Monetary(); amount=fields.Monetary(); currency_id=fields.Many2one(related='company_id.currency_id'); product_id=fields.Many2one('product.product',required=True)
    state=fields.Selection([('draft','Draft'),('active','Active'),('closed','Closed')],default='draft')
    def action_activate(self): self.write({'state':'active'})
    def action_close(self): self.write({'state':'closed'})
    def unlink(self):
        if any(r.state!='draft' for r in self): raise UserError(_('Only draft engagements can be deleted.'))
        return super().unlink()

class LegalTimeEntry(models.Model):
    _name='legal.time.entry'; _description='Legal Time Entry'; _check_company_auto=True
    name=fields.Char(required=True); case_id=fields.Many2one('legal.case',required=True,check_company=True); company_id=fields.Many2one(related='case_id.company_id',store=True,index=True)
    engagement_id=fields.Many2one('legal.engagement',required=True); user_id=fields.Many2one('res.users',default=lambda s:s.env.user,required=True); date=fields.Date(default=fields.Date.today,required=True)
    hours=fields.Float(required=True); rate=fields.Monetary(); amount=fields.Monetary(compute='_compute_amount',store=True); currency_id=fields.Many2one(related='company_id.currency_id'); state=fields.Selection([('draft','Draft'),('billable','Billable'),('invoiced','Invoiced')],default='draft'); invoice_line_id=fields.Many2one('account.move.line',copy=False)
    @api.depends('hours','rate')
    def _compute_amount(self):
        for r in self:r.amount=r.hours*r.rate
    @api.constrains('hours','rate')
    def _positive(self):
        if any(r.hours<=0 or r.rate<0 for r in self):raise ValidationError(_('Hours must be positive and rate cannot be negative.'))
    def action_mark_billable(self): self.write({'state':'billable'})

class LegalExpense(models.Model):
    _name='legal.expense'; _description='Legal Expense'; _check_company_auto=True
    name=fields.Char(required=True); case_id=fields.Many2one('legal.case',required=True,check_company=True); company_id=fields.Many2one(related='case_id.company_id',store=True,index=True); engagement_id=fields.Many2one('legal.engagement',required=True)
    amount=fields.Monetary(required=True); currency_id=fields.Many2one(related='company_id.currency_id'); product_id=fields.Many2one('product.product',required=True); state=fields.Selection([('draft','Draft'),('approved','Approved'),('invoiced','Invoiced')],default='draft'); invoice_line_id=fields.Many2one('account.move.line',copy=False)
    @api.constrains('amount')
    def _positive(self):
        if any(r.amount<=0 for r in self):raise ValidationError(_('Amount must be positive.'))

class LegalTrustAccount(models.Model):
    _name='legal.trust.account'; _description='Client Trust Account'; _check_company_auto=True
    partner_id=fields.Many2one('res.partner',required=True,check_company=True); company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company,index=True); currency_id=fields.Many2one(related='company_id.currency_id')
    transaction_ids=fields.One2many('legal.trust.transaction','trust_account_id'); posted_balance=fields.Monetary(compute='_compute_balances'); available_balance=fields.Monetary(compute='_compute_balances'); state=fields.Selection([('open','Open'),('frozen','Frozen'),('closed','Closed')],default='open')
    _unique=models.Constraint('UNIQUE(partner_id,company_id)','Only one trust account is allowed per client and company.')
    @api.depends('transaction_ids.state','transaction_ids.signed_amount')
    def _compute_balances(self):
        for r in self:r.posted_balance=r.available_balance=sum(r.transaction_ids.filtered(lambda x:x.state=='posted').mapped('signed_amount'))
    def action_freeze(self):self.write({'state':'frozen'})
    def action_unfreeze(self):self.write({'state':'open'})
    def action_close(self):
        if any(not r.currency_id.is_zero(r.available_balance) for r in self):raise UserError(_('A non-zero trust account cannot be closed.'))
        self.write({'state':'closed'})

class LegalTrustTransaction(models.Model):
    _name='legal.trust.transaction'; _description='Trust Transaction'; _inherit=['mail.thread']; _check_company_auto=True; _order='date,id'
    name=fields.Char(default='New',readonly=True,copy=False); trust_account_id=fields.Many2one('legal.trust.account',required=True,check_company=True); company_id=fields.Many2one(related='trust_account_id.company_id',store=True,index=True); partner_id=fields.Many2one(related='trust_account_id.partner_id',store=True)
    case_id=fields.Many2one('legal.case',check_company=True); destination_case_id=fields.Many2one('legal.case',check_company=True,copy=False,help="Where a Case Allocation Transfer moves the money to. The account total does not change; "
         "only which case the client's money is earmarked for."); transaction_type=fields.Selection([('deposit','Deposit'),('apply','Apply to Invoice'),('refund','Refund'),('transfer','Case Allocation Transfer'),('reversal','Reversal')],required=True); amount=fields.Monetary(required=True); currency_id=fields.Many2one(related='company_id.currency_id'); signed_amount=fields.Monetary(compute='_signed',store=True); date=fields.Date(default=fields.Date.today,required=True); reference=fields.Char(); reason=fields.Text(); invoice_id=fields.Many2one('account.move'); move_id=fields.Many2one('account.move',copy=False); reversal_move_id=fields.Many2one('account.move',copy=False); reversed_transaction_id=fields.Many2one('legal.trust.transaction',copy=False); state=fields.Selection([('draft','Draft'),('posted','Posted'),('cancelled','Cancelled')],default='draft')
    @api.depends('amount','transaction_type')
    def _signed(self):
        for r in self:r.signed_amount=r.amount if r.transaction_type=='deposit' else (0 if r.transaction_type=='transfer' else -r.amount)
    @api.constrains('amount')
    def _positive(self):
        if any(r.amount<=0 for r in self):raise ValidationError(_('Amount must be positive.'))

    @api.constrains('transaction_type', 'case_id', 'destination_case_id')
    def _check_transfer_has_two_cases(self):
        """A transfer with no destination moves nothing.

        The type is called Case Allocation Transfer and the help text promises
        it reallocates between the client's cases; without both ends it wrote a
        row that changed no figure, which is worse than not offering it.
        """
        for record in self:
            if record.transaction_type != 'transfer':
                if record.destination_case_id:
                    raise ValidationError(_(
                        'A destination case only applies to a Case Allocation Transfer.'))
                continue
            if not record.case_id or not record.destination_case_id:
                raise ValidationError(_(
                    'A Case Allocation Transfer needs the case the money leaves '
                    'and the case it goes to.'))
            if record.case_id == record.destination_case_id:
                raise ValidationError(_('A transfer must move between two different cases.'))
            clients = {record.case_id.client_id, record.destination_case_id.client_id}
            if clients != {record.partner_id}:
                raise ValidationError(_(
                    "Both cases must belong to the trust account's client. Money held "
                    "for one client cannot be earmarked for another's case."))

    @api.model_create_multi
    def create(self,vals):
        rs=super().create(vals)
        for r in rs:
            if r.name=='New':r.name=self.env['ir.sequence'].next_by_code('legal.trust.transaction') or f'TRUST/{r.id}'
        return rs
    def action_post(self):
        for r in self:
            self.env.cr.execute('SELECT id FROM legal_trust_account WHERE id=%s FOR UPDATE',[r.trust_account_id.id])
            r.trust_account_id.invalidate_recordset()
            if r.transaction_type!='deposit' and r.transaction_type!='transfer' and r.amount>r.trust_account_id.available_balance:raise UserError(_('Insufficient trust balance.'))
            # The account total is unchanged by a transfer, so the balance check
            # above cannot catch moving money a case never held.
            if r.transaction_type=='transfer' and r.amount>r.case_id.trust_allocated_amount:
                raise UserError(_('Case %(case)s holds %(held)s in trust; %(asked)s cannot be moved from it.',
                                  case=r.case_id.display_name,
                                  held=r.case_id.trust_allocated_amount,
                                  asked=r.amount))
            if r.transaction_type!='deposit' and r.trust_account_id.state!='open':raise UserError(_('The trust account is not open.'))
            c=r.company_id; debit = c.legal_trust_bank_account_id if r.transaction_type=='deposit' else c.legal_trust_liability_account_id; credit=c.legal_trust_liability_account_id if r.transaction_type=='deposit' else (c.legal_trust_receivable_account_id if r.transaction_type=='apply' else c.legal_trust_bank_account_id)
            if r.transaction_type!='transfer':
                if not c.legal_trust_journal_id or not debit or not credit:raise UserError(_('Configure the trust journal and accounts first.'))
                mv=self.env['account.move'].create({'journal_id':c.legal_trust_journal_id.id,'date':r.date,'ref':r.reference or r.name,'legal_case_id':r.case_id.id,'line_ids':[(0,0,{'name':r.name,'account_id':debit.id,'partner_id':r.partner_id.id,'debit':r.amount}),(0,0,{'name':r.name,'account_id':credit.id,'partner_id':r.partner_id.id,'credit':r.amount})]}); mv.action_post(); r.move_id=mv
            r.state='posted'
            if r.transaction_type=='apply':
                if not r.invoice_id or r.invoice_id.state!='posted' or r.invoice_id.partner_id.commercial_partner_id != r.partner_id.commercial_partner_id:
                    raise UserError(_('A posted invoice for the same client is required.'))
                receivable=r.move_id.line_ids.filtered(lambda l:l.account_id.account_type=='asset_receivable' and not l.reconciled)
                invoice_lines=r.invoice_id.line_ids.filtered(lambda l:l.account_id.account_type=='asset_receivable' and not l.reconciled)
                (receivable | invoice_lines).reconcile()
            self.env['legal.audit.log'].log(r,'post',['transaction_type','amount','case_id','invoice_id'])
    def action_cancel(self):
        for r in self:
            if r.state!='posted':
                r.state='cancelled'; continue
            if not r.move_id:
                r.state='cancelled'; continue
            reversal=r.move_id._reverse_moves([{'ref':_('Reversal of %s') % r.name}],cancel=True)
            r.write({'state':'cancelled','reversal_move_id':reversal.id})
            self.env['legal.audit.log'].log(r,'reverse',['state','reversal_move_id'])
    def unlink(self):
        if any(r.state=='posted' for r in self):raise UserError(_('Posted trust transactions cannot be deleted.'))
        return super().unlink()


class LegalCaseTrustAllocation(models.Model):
    """What the client's trust money is earmarked for, case by case.

    The account balance says how much is held for the client; it never said
    how much of it belongs to which matter. Without that figure the
    "Case Allocation Transfer" had nothing to move — it wrote a row and
    changed no number — and a lawyer could not answer the ordinary question
    of whether a case is funded.
    """
    _inherit = 'legal.case'

    trust_allocated_amount = fields.Monetary(
        string='Trust Allocated', compute='_compute_trust_allocated_amount',
        currency_field='company_currency_id',
        help="Client money held in trust and earmarked for this case. Deposits "
             "add to it, applying it to an invoice or refunding it takes it "
             "away, and a Case Allocation Transfer moves it between cases "
             "without changing the account total.")
    company_currency_id = fields.Many2one(
        related='company_id.currency_id', string='Company Currency')
    # Declared so the compute has real dependencies. A search() inside a
    # compute cannot be tracked by the ORM: the figure went stale the moment a
    # transaction was posted, and showed the balance from before the transfer.
    trust_transaction_ids = fields.One2many(
        'legal.trust.transaction', 'case_id', string='Trust Movements')
    trust_transfer_in_ids = fields.One2many(
        'legal.trust.transaction', 'destination_case_id',
        string='Trust Transfers Received')

    @api.depends(
        'trust_transaction_ids.state', 'trust_transaction_ids.amount',
        'trust_transaction_ids.signed_amount', 'trust_transaction_ids.transaction_type',
        'trust_transfer_in_ids.state', 'trust_transfer_in_ids.amount',
        'trust_transfer_in_ids.transaction_type')
    def _compute_trust_allocated_amount(self):
        for case in self:
            posted_out = case.trust_transaction_ids.filtered(
                lambda t: t.state == 'posted')
            total = sum(
                -t.amount if t.transaction_type == 'transfer' else t.signed_amount
                for t in posted_out)
            total += sum(
                t.amount for t in case.trust_transfer_in_ids
                if t.state == 'posted' and t.transaction_type == 'transfer')
            case.trust_allocated_amount = total
