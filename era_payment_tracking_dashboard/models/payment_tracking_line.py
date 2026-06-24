# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class PaymentTrackingLine(models.Model):
    _name = 'payment.tracking.line'
    _description = 'سطر قسط متابعة الدفعات'
    _order = 'tracking_id, due_date, sequence, id'

    tracking_id = fields.Many2one(
        'payment.tracking', string='متابعة الدفعات', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        'res.company', string='الشركة',
        related='tracking_id.company_id', store=True, index=True,
    )
    sequence = fields.Integer(string='التسلسل', default=10)
    name = fields.Char(string='البيان')
    payment_term_line_id = fields.Many2one(
        'account.payment.term.line', string='سطر سياسة الدفع', ondelete='set null',
    )
    currency_id = fields.Many2one(
        'res.currency', string='العملة',
        related='tracking_id.currency_id', store=True,
    )

    amount_expected = fields.Monetary(
        string='المبلغ المتوقع', currency_field='currency_id',
    )
    amount_paid = fields.Monetary(
        string='المبلغ المدفوع', compute='_compute_amount_paid', store=True,
        currency_field='currency_id',
    )
    amount_due = fields.Monetary(
        string='المتبقي', compute='_compute_amount_paid', store=True,
        currency_field='currency_id',
    )

    due_date = fields.Date(string='تاريخ الاستحقاق')
    payment_date = fields.Date(
        string='تاريخ الدفع', compute='_compute_state', store=True,
    )
    days_overdue = fields.Integer(
        string='أيام التأخير', compute='_compute_state', store=True,
    )
    state = fields.Selection(
        selection=[
            ('upcoming', 'قادم'),
            ('unpaid', 'غير مدفوع'),
            ('partial', 'مدفوع جزئيًا'),
            ('paid', 'مدفوع'),
            ('overdue', 'متأخر'),
        ],
        string='الحالة', compute='_compute_state', store=True, default='upcoming',
    )

    @api.depends(
        'tracking_id.amount_paid', 'amount_expected', 'due_date', 'sequence',
        'tracking_id.installment_line_ids.amount_expected',
        'tracking_id.installment_line_ids.due_date',
    )
    def _compute_amount_paid(self):
        """يوزّع إجمالي المدفوع على الأقساط بترتيب الاستحقاق (الأقدم أولًا)."""
        for line in self:
            siblings = line.tracking_id.installment_line_ids.sorted(
                key=lambda l: (
                    l.due_date or fields.Date.from_string('9999-12-31'),
                    l.sequence,
                    l.id,
                )
            )
            total_paid = line.tracking_id.amount_paid or 0.0
            prior_expected = 0.0
            for sibling in siblings:
                if sibling.id == line.id:
                    break
                prior_expected += sibling.amount_expected or 0.0
            remaining = total_paid - prior_expected
            paid = max(0.0, min(line.amount_expected or 0.0, remaining))
            line.amount_paid = paid
            line.amount_due = (line.amount_expected or 0.0) - paid

    @api.depends(
        'amount_paid', 'amount_expected', 'due_date',
        'tracking_id.last_payment_date',
    )
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for line in self:
            expected = line.amount_expected or 0.0
            paid = line.amount_paid or 0.0
            # الحالة
            if expected and paid >= expected:
                line.state = 'paid'
            elif line.due_date and line.due_date > today:
                line.state = 'upcoming'
            elif line.due_date and line.due_date < today and paid <= 0.0:
                line.state = 'overdue'
            elif 0.0 < paid < expected:
                line.state = 'partial'
            else:
                line.state = 'unpaid'
            # أيام التأخير
            if line.due_date and line.due_date < today and paid < expected:
                line.days_overdue = (today - line.due_date).days
            else:
                line.days_overdue = 0
            # تاريخ الدفع الفعلي (تقريبي من آخر دفعة على مستوى السجل)
            if line.state == 'paid':
                line.payment_date = line.tracking_id.last_payment_date
            else:
                line.payment_date = False
