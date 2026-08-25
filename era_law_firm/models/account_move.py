from odoo import fields, models
class AccountMove(models.Model):
    _inherit='account.move'
    legal_case_id=fields.Many2one('legal.case',check_company=True,index=True)
    legal_engagement_id=fields.Many2one('legal.engagement',check_company=True,index=True)
    def button_cancel(self):
        result=super().button_cancel()
        for line in self.invoice_line_ids:
            for field,state in (('legal_time_entry_id','billable'),('legal_expense_id','approved'),('legal_milestone_id','ready'),('legal_success_fee_id','approved')):
                source=line[field]
                if source and source.invoice_line_id==line:
                    source.write({'state':state,'invoice_line_id':False})
        return result
