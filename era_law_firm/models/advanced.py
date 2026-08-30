import hashlib
import json
import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


def normalize_legal_text(value):
    value = (value or '').strip().lower()
    value = re.sub(r'[\u064b-\u065f\u0670\u0640]', '', value)
    value = value.translate(str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ى': 'ي', 'ة': 'ه'}))
    return re.sub(r'\s+', ' ', value)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def can_access_legal_case(self, case):
        self.ensure_one()
        return bool(case.company_id in self.company_ids and (
            self.has_group('era_law_firm.group_legal_supervisor') or
            case.lawyer_id == self or self in case.team_user_ids))

    def can_view_legal_identity(self):
        self.ensure_one()
        return self.has_group('era_law_firm.group_legal_manager')

    def can_view_restricted_legal_content(self):
        self.ensure_one()
        return self.has_group('era_law_firm.group_legal_lawyer')

    def can_supervise_legal_cases(self):
        """Supervisors see every file of their company, not only their own."""
        self.ensure_one()
        return self.has_group('era_law_firm.group_legal_supervisor')


class LegalCase(models.Model):
    _inherit = 'legal.case'

    close_date = fields.Date(readonly=True, tracking=True)
    outcome = fields.Text(tracking=True)
    engagement_ids = fields.One2many('legal.engagement', 'case_id')
    consultation_ids = fields.One2many('legal.consultation', 'case_id')
    time_entry_ids = fields.One2many('legal.time.entry', 'case_id')
    expense_ids = fields.One2many('legal.expense', 'case_id')
    invoice_ids = fields.One2many('account.move', 'legal_case_id')
    # Stored so they can be searched, grouped and used as pivot/graph measures.
    hearing_count = fields.Integer(compute='_compute_case_metrics', store=True)
    document_count = fields.Integer(compute='_compute_case_metrics', store=True)
    billable_hours = fields.Float(compute='_compute_case_metrics', store=True)
    invoiced_amount = fields.Monetary(compute='_compute_case_metrics', store=True)
    paid_amount = fields.Monetary(compute='_compute_case_metrics', store=True)
    outstanding_amount = fields.Monetary(compute='_compute_case_metrics', store=True)
    expense_amount = fields.Monetary(compute='_compute_case_metrics', store=True)

    @api.depends('hearing_ids', 'document_ids',
                 'time_entry_ids.hours', 'time_entry_ids.state',
                 'expense_ids.amount', 'expense_ids.state',
                 'invoice_ids.amount_total_signed', 'invoice_ids.amount_residual_signed',
                 'invoice_ids.state', 'invoice_ids.move_type')
    def _compute_case_metrics(self):
        for rec in self:
            invoices = rec.invoice_ids.filtered(lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
            rec.hearing_count = len(rec.hearing_ids)
            rec.document_count = len(rec.document_ids)
            rec.billable_hours = sum(rec.time_entry_ids.filtered(lambda x: x.state != 'draft').mapped('hours'))
            rec.expense_amount = sum(rec.expense_ids.filtered(lambda x: x.state != 'draft').mapped('amount'))
            rec.invoiced_amount = sum(invoices.mapped('amount_total_signed'))
            rec.outstanding_amount = sum(invoices.mapped('amount_residual_signed'))
            rec.paid_amount = rec.invoiced_amount - rec.outstanding_amount

    def action_close(self):
        result = super().action_close()
        self.write({'close_date': fields.Date.today()})
        return result

    def action_reopen(self):
        self.write({'state': 'confirmed', 'close_date': False})

    def write(self, vals):
        if 'company_id' in vals:
            for rec in self:
                if rec.company_id.id != vals['company_id'] and (rec.hearing_ids or rec.document_ids or rec.engagement_ids):
                    raise UserError(_('The company cannot be changed after related legal records exist.'))
        sensitive = {'client_id', 'party_ids'} & set(vals)
        result = super().write(vals)
        if sensitive:
            self.write({'conflict_check_id': False})
        return result


class LegalCaseParty(models.Model):
    _inherit = 'legal.case.party'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('case_id').write({'conflict_check_id': False})
        return records

    def write(self, vals):
        cases = self.mapped('case_id')
        result = super().write(vals)
        if {'partner_id', 'role'} & set(vals):
            cases.write({'conflict_check_id': False})
        return result


class LegalConflictCheck(models.Model):
    _inherit = 'legal.conflict.check'

    @api.model
    def _normalize_arabic_text(self, value):
        return normalize_legal_text(value)

    def action_clear(self):
        self.write({'state': 'draft', 'party_signature': False, 'line_ids': [(5, 0, 0)]})

    def action_approve(self):
        blocked = self.filtered(lambda r: r.line_ids)
        if blocked:
            raise UserError(_('A conflict check with matches cannot be cleared without a manager override.'))
        self.write({'state': 'clear', 'party_signature': self.case_id._party_signature()})


class LegalDeadlineRule(models.Model):
    _name = 'legal.deadline.rule'
    _description = 'Legal Deadline Rule'
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    company_id = fields.Many2one('res.company', index=True)
    days = fields.Integer(required=True)
    start_point = fields.Selection([('judgment', 'Judgment Date'), ('notification', 'Notification Date'), ('manual', 'Manual')], required=True)
    legal_reference = fields.Char(required=True)
    warning = fields.Text(translate=True)
    active = fields.Boolean(default=True)

    @api.constrains('days')
    def _check_days(self):
        if any(r.days <= 0 for r in self):
            raise ValidationError(_('Deadline rule days must be positive.'))


class LegalDeadline(models.Model):
    _inherit = 'legal.deadline'

    rule_id = fields.Many2one('legal.deadline.rule')
    start_date = fields.Date()
    suggested_date = fields.Date(compute='_compute_suggested_date', store=True)
    overdue_state = fields.Selection([('future', 'Future'), ('due', 'Due Today'), ('overdue', 'Overdue'), ('done', 'Done')], compute='_compute_overdue_state')

    @api.depends('rule_id.days', 'start_date')
    def _compute_suggested_date(self):
        for rec in self:
            rec.suggested_date = fields.Date.add(rec.start_date, days=rec.rule_id.days) if rec.start_date and rec.rule_id else False

    @api.depends('deadline_date', 'state')
    def _compute_overdue_state(self):
        today = fields.Date.today()
        for rec in self:
            rec.overdue_state = 'done' if rec.state in ('done', 'cancelled') else ('future' if not rec.deadline_date else ('overdue' if rec.deadline_date < today else ('due' if rec.deadline_date == today else 'future')))

    def action_adopt_suggested_date(self):
        if any(not r.suggested_date for r in self):
            raise UserError(_('No suggested date is available.'))
        for rec in self:
            rec.deadline_date = rec.suggested_date


class LegalEngagementMilestone(models.Model):
    _name = 'legal.engagement.milestone'
    _description = 'Legal Engagement Milestone'
    _check_company_auto = True
    _order = 'due_date, id'

    name = fields.Char(required=True)
    engagement_id = fields.Many2one('legal.engagement', required=True, ondelete='cascade', check_company=True)
    company_id = fields.Many2one(related='engagement_id.company_id', store=True, index=True)
    due_date = fields.Date()
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(related='engagement_id.currency_id')
    invoice_line_id = fields.Many2one('account.move.line', copy=False, readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('ready', 'Ready'), ('invoiced', 'Invoiced')], default='draft')


class LegalEngagement(models.Model):
    _inherit = 'legal.engagement'
    milestone_ids = fields.One2many('legal.engagement.milestone', 'engagement_id')
    success_fee_ids = fields.One2many('legal.success.fee', 'engagement_id')

    @api.constrains('billing_type', 'hourly_rate', 'amount')
    def _check_billing_configuration(self):
        for rec in self:
            if rec.billing_type == 'hourly' and rec.hourly_rate <= 0:
                raise ValidationError(_('An hourly engagement requires a positive hourly rate.'))
            if rec.billing_type == 'fixed' and rec.amount <= 0:
                raise ValidationError(_('A fixed engagement requires a positive amount.'))


class LegalSuccessFee(models.Model):
    _name = 'legal.success.fee'
    _description = 'Legal Success Fee'
    _inherit = ['mail.thread']
    _check_company_auto = True

    name = fields.Char(required=True)
    engagement_id = fields.Many2one('legal.engagement', required=True, check_company=True)
    case_id = fields.Many2one(related='engagement_id.case_id', store=True)
    company_id = fields.Many2one(related='engagement_id.company_id', store=True, index=True)
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    evidence = fields.Text(required=True)
    state = fields.Selection([('draft', 'Draft'), ('review', 'In Review'), ('approved', 'Approved'), ('invoiced', 'Invoiced')], default='draft', tracking=True)
    invoice_line_id = fields.Many2one('account.move.line', copy=False, readonly=True)

    def action_submit(self): self.write({'state': 'review'})
    def action_approve(self):
        if not self.env.user.has_group('era_law_firm.group_legal_manager'):
            raise AccessError(_('Only a legal manager may approve a success fee.'))
        self.write({'state': 'approved'})


class LegalConsultation(models.Model):
    _name = 'legal.consultation'
    _description = 'Legal Consultation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    partner_id = fields.Many2one('res.partner', required=True, check_company=True)
    lawyer_id = fields.Many2one('res.users', required=True)
    consultation_date = fields.Datetime(required=True, default=fields.Datetime.now)
    notes = fields.Html(groups='era_law_firm.group_legal_lawyer,era_law_firm.group_legal_manager')
    state = fields.Selection([('draft', 'Draft'), ('scheduled', 'Scheduled'), ('done', 'Done'), ('converted', 'Converted'), ('cancelled', 'Cancelled')], default='draft', tracking=True)
    case_id = fields.Many2one('legal.case', readonly=True, copy=False)

    def action_convert_case(self):
        self.ensure_one()
        if self.case_id:
            raise UserError(_('This consultation has already been converted.'))
        stage = self.env.ref('era_law_firm.stage_intake')
        case = self.env['legal.case'].create({'client_id': self.partner_id.id, 'lawyer_id': self.lawyer_id.id, 'case_type': 'consultation', 'stage_id': stage.id, 'company_id': self.company_id.id})
        self.write({'case_id': case.id, 'state': 'converted'})
        return {'type': 'ir.actions.act_window', 'res_model': 'legal.case', 'res_id': case.id, 'view_mode': 'form'}


class LegalAuditLog(models.Model):
    _name = 'legal.audit.log'
    _description = 'Legal Audit Log'
    _order = 'event_date desc, id desc'

    event_date = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
    user_id = fields.Many2one('res.users', required=True, default=lambda s: s.env.user, readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, readonly=True, index=True)
    model_name = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    operation = fields.Char(required=True, readonly=True)
    changed_fields = fields.Text(readonly=True)
    fingerprint = fields.Char(readonly=True)

    @api.model
    def log(self, record, operation, changed_fields=None):
        names = sorted(set(changed_fields or []) - {'legal_identity_number', 'legal_registration_number', 'callback_secret'})
        raw = json.dumps([record._name, record.id, operation, names, fields.Datetime.now().isoformat()])
        return self.sudo().create({'company_id': getattr(record, 'company_id', self.env.company).id, 'model_name': record._name, 'res_id': record.id, 'operation': operation, 'changed_fields': ','.join(names), 'fingerprint': hashlib.sha256(raw.encode()).hexdigest()})

    def write(self, vals): raise AccessError(_('Audit logs cannot be modified.'))
    def unlink(self): raise AccessError(_('Audit logs cannot be deleted.'))


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'
    legal_time_entry_id = fields.Many2one('legal.time.entry', copy=False, index=True)
    legal_expense_id = fields.Many2one('legal.expense', copy=False, index=True)
    legal_milestone_id = fields.Many2one('legal.engagement.milestone', copy=False, index=True)
    legal_success_fee_id = fields.Many2one('legal.success.fee', copy=False, index=True)
