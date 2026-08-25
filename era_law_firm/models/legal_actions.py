"""Window actions and form helpers that make the model layer reachable from the UI.

Everything here is presentation glue: opening a wizard with the right defaults,
running a check and showing its result, turning a stat button into a filtered
list. The business rules themselves stay in legal_core / legal_finance / advanced.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LegalCase(models.Model):
    _inherit = 'legal.case'

    date_filed = fields.Date(tracking=True, help='Date the case was filed with the court.')
    conflict_state = fields.Selection(related='conflict_check_id.state', string='Conflict Status')

    def _related_action(self, name, model, domain, context=None):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': 'list,form',
            'domain': domain,
            'context': dict(context or {}, default_case_id=self.id),
        }

    def action_run_conflict_check(self):
        """Create the check if the case has none, run it, and show the result."""
        self.ensure_one()
        check = self.conflict_check_id or self.env['legal.conflict.check'].create({'case_id': self.id})
        check.action_run_check()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Conflict Check'),
            'res_model': 'legal.conflict.check',
            'res_id': check.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_conflict_check(self):
        self.ensure_one()
        if not self.conflict_check_id:
            return self.action_run_conflict_check()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Conflict Check'),
            'res_model': 'legal.conflict.check',
            'res_id': self.conflict_check_id.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_invoice_wizard(self):
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError(_('Only a confirmed case can be invoiced.'))
        engagement = self.engagement_ids.filtered(lambda e: e.state == 'active')[:1]
        if not engagement:
            raise UserError(_('This case has no active engagement to invoice.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Legal Invoice'),
            'res_model': 'legal.invoice.create.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_case_id': self.id, 'default_engagement_id': engagement.id},
        }

    def action_view_hearings(self):
        return self._related_action(_('Hearings'), 'legal.hearing', [('case_id', '=', self.id)])

    def action_view_documents(self):
        return self._related_action(_('Documents'), 'legal.document', [('case_id', '=', self.id)])

    def action_view_deadlines(self):
        return self._related_action(_('Deadlines'), 'legal.deadline', [('case_id', '=', self.id)])

    def action_view_time_entries(self):
        return self._related_action(_('Time Entries'), 'legal.time.entry', [('case_id', '=', self.id)])

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoices'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('legal_case_id', '=', self.id)],
            'context': {'default_legal_case_id': self.id, 'default_move_type': 'out_invoice',
                        'default_partner_id': self.client_id.id},
        }


class LegalTrustAccount(models.Model):
    _inherit = 'legal.trust.account'

    def action_open_operation_wizard(self):
        """Open the trust wizard. The caller sets `trust_operation` in the context."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Trust Operation'),
            'res_model': 'legal.trust.operation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_trust_account_id': self.id,
                'default_transaction_type': self.env.context.get('trust_operation', 'deposit'),
            },
        }

    def action_view_transactions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Trust Transactions'),
            'res_model': 'legal.trust.transaction',
            'view_mode': 'list,form',
            'domain': [('trust_account_id', '=', self.id)],
            'context': {'default_trust_account_id': self.id},
        }


class LegalDocument(models.Model):
    """Attach the file from the form instead of hunting for an existing ir.attachment."""
    _inherit = 'legal.document'

    file_data = fields.Binary(string='File', compute='_compute_file_data', inverse='_inverse_file_data')
    file_name = fields.Char(string='File Name', compute='_compute_file_data', inverse='_inverse_file_data')

    @api.depends('attachment_id')
    def _compute_file_data(self):
        for rec in self:
            rec.file_data = rec.attachment_id.datas
            rec.file_name = rec.attachment_id.name

    def _inverse_file_data(self):
        for rec in self:
            if not rec.file_data:
                continue
            name = rec.file_name or rec.name or 'document'
            if rec.attachment_id:
                rec.attachment_id.write({'datas': rec.file_data, 'name': name})
            else:
                rec.attachment_id = self.env['ir.attachment'].create({
                    'name': name, 'datas': rec.file_data,
                    'res_model': rec._name, 'res_id': rec.id,
                })

    @api.model_create_multi
    def create(self, vals_list):
        # attachment_id is required at the database level, so the attachment has to
        # exist before the row does -- the inverse would run too late.
        for vals in vals_list:
            data = vals.pop('file_data', False)
            name = vals.pop('file_name', False)
            if data and not vals.get('attachment_id'):
                vals['attachment_id'] = self.env['ir.attachment'].create({
                    'name': name or vals.get('name') or 'document',
                    'datas': data,
                }).id
        return super().create(vals_list)


class LegalExpense(models.Model):
    """The model defines an `approved` state but shipped no way to reach it."""
    _inherit = 'legal.expense'

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_reset_draft(self):
        if any(r.invoice_line_id for r in self):
            raise UserError(_('An invoiced expense cannot be reset to draft.'))
        self.write({'state': 'draft'})


class LegalEngagementMilestone(models.Model):
    """Same gap: `ready` is what the invoice wizard looks for, with no way to set it."""
    _inherit = 'legal.engagement.milestone'

    def action_set_ready(self):
        self.write({'state': 'ready'})

    def action_reset_draft(self):
        if any(r.invoice_line_id for r in self):
            raise UserError(_('An invoiced milestone cannot be reset to draft.'))
        self.write({'state': 'draft'})
