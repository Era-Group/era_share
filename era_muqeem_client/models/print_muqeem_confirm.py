from odoo import models, fields, _,api

class PrintMuqeemConfirm(models.TransientModel):
    _name = "print.muqeem.confirm"
    _description = "Confirm Printing Muqeem Report"

    message = fields.Text(
        string="Message",
        default=lambda self: _("The cost of printing a Muqeem report is 125 points. Do you want to continue?"),
        readonly=True
    )

    # def action_confirm(self):
    #     """ ينفذ الدالة print_muqeem_report من الويزارد الأساسي """
    #     active_id = self.env.context.get('active_id')
    #     if not active_id:
    #         return {'type': 'ir.actions.act_window_close'}
    #
    #     wizard = self.env['print.muqeem.report'].browse(active_id)
    #     return wizard.print_muqeem_report()
    #
    # def action_cancel(self):
    #     return {'type': 'ir.actions.act_window_close'}

    def action_open_muqeem_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'print.muqeem.report',
            'view_mode': 'form',
            'target': 'new',
            'context': dict(self._context or {}, active_id=self._context.get('active_id')),
        }