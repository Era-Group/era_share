from odoo import fields, models, _
from odoo.exceptions import UserError
class LegalInvoiceCreateWizard(models.TransientModel):
    _name='legal.invoice.create.wizard'; _description='Create Legal Invoice'
    case_id=fields.Many2one('legal.case',required=True); engagement_id=fields.Many2one('legal.engagement',required=True); time_entry_ids=fields.Many2many('legal.time.entry'); expense_ids=fields.Many2many('legal.expense')
    milestone_ids=fields.Many2many('legal.engagement.milestone'); success_fee_ids=fields.Many2many('legal.success.fee')
    def action_create_invoice(self):
        self.ensure_one()
        if self.engagement_id.state!='active':raise UserError(_('The engagement must be active.'))
        times=self.time_entry_ids.filtered(lambda x:x.state=='billable' and not x.invoice_line_id)
        expenses=self.expense_ids.filtered(lambda x:x.state=='approved' and not x.invoice_line_id)
        milestones=self.milestone_ids.filtered(lambda x:x.state=='ready' and not x.invoice_line_id)
        fees=self.success_fee_ids.filtered(lambda x:x.state=='approved' and not x.invoice_line_id)
        if not (times or expenses or milestones or fees):raise UserError(_('There is nothing to invoice.'))
        for model, records in (('legal_time_entry', times), ('legal_expense', expenses), ('legal_engagement_milestone', milestones), ('legal_success_fee', fees)):
            if records:
                self.env.cr.execute('SELECT id FROM %s WHERE id IN %%s FOR UPDATE' % model, [tuple(records.ids)])
                records.invalidate_recordset()
                if any(r.invoice_line_id for r in records):
                    raise UserError(_('A selected source has already been invoiced.'))
        lines=[]
        for s in times:lines.append((0,0,{'product_id':s.engagement_id.product_id.id,'name':s.name,'quantity':s.hours,'price_unit':s.rate}))
        for s in expenses:lines.append((0,0,{'product_id':s.product_id.id,'name':s.name,'quantity':1,'price_unit':s.amount}))
        for s in milestones:lines.append((0,0,{'product_id':s.engagement_id.product_id.id,'name':s.name,'quantity':1,'price_unit':s.amount}))
        for s in fees:lines.append((0,0,{'product_id':s.engagement_id.product_id.id,'name':s.name,'quantity':1,'price_unit':s.amount}))
        move=self.env['account.move'].create({'move_type':'out_invoice','partner_id':self.case_id.client_id.id,'legal_case_id':self.case_id.id,'legal_engagement_id':self.engagement_id.id,'invoice_line_ids':lines})
        for source,line in zip(list(times) + list(expenses) + list(milestones) + list(fees),move.invoice_line_ids):
            source.write({'state':'invoiced','invoice_line_id':line.id})
            field={'legal.time.entry':'legal_time_entry_id','legal.expense':'legal_expense_id','legal.engagement.milestone':'legal_milestone_id','legal.success.fee':'legal_success_fee_id'}[source._name]
            line[field]=source.id
        return {'type':'ir.actions.act_window','res_model':'account.move','res_id':move.id,'view_mode':'form'}
class LegalTrustOperationWizard(models.TransientModel):
    _name='legal.trust.operation.wizard'; _description='Trust Operation'
    trust_account_id=fields.Many2one('legal.trust.account',required=True); partner_id=fields.Many2one(related='trust_account_id.partner_id'); case_id=fields.Many2one('legal.case'); transaction_type=fields.Selection([('deposit','Deposit'),('apply','Apply to Invoice'),('refund','Refund'),('transfer','Transfer')],required=True); amount=fields.Monetary(required=True); currency_id=fields.Many2one(related='trust_account_id.currency_id'); invoice_id=fields.Many2one('account.move'); reference=fields.Char(); reason=fields.Text()
    def action_apply(self):
        self.ensure_one()
        if self.transaction_type=='refund' and (not self.reason or not self.reference):raise UserError(_('Refund reason and payment reference are required.'))
        tx=self.env['legal.trust.transaction'].create({'trust_account_id':self.trust_account_id.id,'case_id':self.case_id.id,'transaction_type':self.transaction_type,'amount':self.amount,'invoice_id':self.invoice_id.id,'reference':self.reference,'reason':self.reason});tx.action_post()
        return {'type':'ir.actions.act_window','res_model':'legal.trust.transaction','res_id':tx.id,'view_mode':'form'}
