# -*- coding: utf-8 -*-
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.date_utils import parse_date


class PaymentTracking(models.Model):
    _name = 'payment.tracking'
    _description = 'متابعة الدفعات من العملاء'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, next_payment_date asc, id desc'
    _rec_name = 'name'

    # ------------------------------------------------------------------
    # الحقول الأساسية
    # ------------------------------------------------------------------
    name = fields.Char(
        string='المرجع', required=True, copy=False, readonly=True,
        index=True, default=lambda self: _('New'), tracking=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order', string='أمر البيع', required=True,
        domain="[('state', '=', 'sale')]", ondelete='cascade',
        tracking=True, index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='العميل',
        related='sale_order_id.partner_id', store=True, index=True, tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', string='الشركة',
        related='sale_order_id.company_id', store=True, index=True,
    )
    currency_id = fields.Many2one(
        'res.currency', string='العملة',
        related='sale_order_id.currency_id', store=True,
    )
    payment_term_id = fields.Many2one(
        'account.payment.term', string='سياسة الدفع',
        related='sale_order_id.payment_term_id', store=True,
    )

    # ------------------------------------------------------------------
    # حقول المبالغ
    # ------------------------------------------------------------------
    amount_total = fields.Monetary(
        string='إجمالي الطلب', compute='_compute_amounts', store=True,
        currency_field='currency_id', tracking=True,
    )
    amount_invoiced = fields.Monetary(
        string='المفوتر', compute='_compute_amounts', store=True,
        currency_field='currency_id',
    )
    amount_paid = fields.Monetary(
        string='المدفوع', compute='_compute_amounts', store=True,
        currency_field='currency_id', tracking=True,
    )
    amount_due = fields.Monetary(
        string='المتبقي', compute='_compute_amounts', store=True,
        currency_field='currency_id', tracking=True,
    )
    payment_percentage = fields.Float(
        string='نسبة السداد', compute='_compute_amounts', store=True,
        aggregator='avg',
    )

    # ------------------------------------------------------------------
    # العلاقات
    # ------------------------------------------------------------------
    invoice_ids = fields.Many2many(
        'account.move', string='الفواتير',
        compute='_compute_invoices', store=True,
    )
    invoice_count = fields.Integer(
        string='عدد الفواتير', compute='_compute_invoices', store=True,
    )
    payment_ids = fields.Many2many(
        'account.payment', string='الدفعات',
        compute='_compute_payments', store=True,
    )
    payment_count = fields.Integer(
        string='عدد الدفعات', compute='_compute_payments', store=True,
    )
    installment_line_ids = fields.One2many(
        'payment.tracking.line', 'tracking_id', string='الأقساط المتوقعة',
    )

    # ------------------------------------------------------------------
    # التواريخ
    # ------------------------------------------------------------------
    order_date = fields.Date(
        string='تاريخ الطلب', compute='_compute_dates', store=True,
    )
    first_invoice_date = fields.Date(
        string='تاريخ أول فاتورة', compute='_compute_dates', store=True,
    )
    next_payment_date = fields.Date(
        string='تاريخ الدفعة القادمة', compute='_compute_dates', store=True,
        tracking=True,
    )
    next_payment_amount = fields.Monetary(
        string='مبلغ الدفعة القادمة', compute='_compute_dates', store=True,
        currency_field='currency_id',
    )
    last_payment_date = fields.Date(
        string='تاريخ آخر دفعة', compute='_compute_dates', store=True,
    )
    # آخر نشاط على السجل: الأكبر بين تاريخ آخر تعديل، أو أحدث نشاط (activity)،
    # أو أحدث رسالة/ملاحظة في الشاتر. حقل غير مخزَّن وله دالة بحث مخصّصة تُشغّل
    # فلاتر «محدثة خلال يوم / محدثة هذا الأسبوع».
    last_activity_on = fields.Datetime(
        string='آخر نشاط', compute='_compute_last_activity_on',
        search='_search_last_activity_on',
    )

    # ------------------------------------------------------------------
    # الحالة
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection=[
            ('pending', 'بانتظار الدفع'),
            ('in_progress', 'جاري الدفع'),
            ('paid', 'مدفوع بالكامل'),
            ('overdue', 'متأخر'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة', compute='_compute_state', store=True,
        default='pending', tracking=True, index=True,
    )
    priority = fields.Selection(
        selection=[
            ('0', 'منخفض'),
            ('1', 'عادي'),
            ('2', 'عالي'),
            ('3', 'عاجل'),
        ],
        string='الأولوية', default='1', tracking=True,
    )
    notes = fields.Html(string='ملاحظات')

    # ------------------------------------------------------------------
    # معلومات الاتصال
    # ------------------------------------------------------------------
    partner_email = fields.Char(
        string='البريد الإلكتروني', related='partner_id.email', readonly=False,
    )
    partner_phone = fields.Char(
        string='الهاتف', related='partner_id.phone', readonly=False,
    )
    # ملاحظة: أزالت أودو 19 الحقل mobile من res.partner ولم يبقَ سوى phone، لذا
    # يعكس رقم الجوال (المستخدَم لإرسال واتساب) حقل الهاتف الموحّد للعميل.
    partner_mobile = fields.Char(
        string='الجوال (واتساب)', related='partner_id.phone', readonly=True,
    )
    # حقل مساعد لإظهار/إخفاء زر «واتساب (WAHA)»: صحيح فقط إن كانت لشركة السجل
    # جلسة واتساب (WAHA) افتراضية بحالة «تعمل». مُحاط بحارس ليبقى آمنًا حتى لو
    # لم تكن وحدة WAHA مثبّتة (عندها يبقى الزر مخفيًا دائمًا).
    waha_session_active = fields.Boolean(
        string='جلسة WAHA فعّالة', compute='_compute_waha_session_active',
    )

    # ------------------------------------------------------------------
    # إحصائيات الأقساط
    # ------------------------------------------------------------------
    installment_count = fields.Integer(
        string='عدد الأقساط', compute='_compute_installment_stats', store=True,
    )
    paid_installments = fields.Integer(
        string='الأقساط المدفوعة', compute='_compute_installment_stats', store=True,
    )
    overdue_installments = fields.Integer(
        string='الأقساط المتأخرة', compute='_compute_installment_stats', store=True,
    )
    days_overdue = fields.Integer(
        string='أيام التأخير', compute='_compute_installment_stats', store=True,
    )

    # ==================================================================
    # الدوال المحسوبة
    # ==================================================================
    @api.depends(
        'sale_order_id.invoice_ids',
        'sale_order_id.invoice_ids.move_type',
        'sale_order_id.invoice_ids.state',
    )
    def _compute_invoices(self):
        for rec in self:
            invoices = rec.sale_order_id.invoice_ids.filtered(
                lambda m: m.move_type == 'out_invoice'
            )
            rec.invoice_ids = invoices
            rec.invoice_count = len(invoices)

    @api.depends('invoice_ids', 'invoice_ids.matched_payment_ids')
    def _compute_payments(self):
        for rec in self:
            payments = rec.invoice_ids.matched_payment_ids
            rec.payment_ids = payments
            rec.payment_count = len(payments)

    @api.depends(
        'sale_order_id.amount_total',
        'invoice_ids',
        'invoice_ids.amount_total',
        'invoice_ids.amount_residual',
        'invoice_ids.state',
    )
    def _compute_amounts(self):
        for rec in self:
            rec.amount_total = rec.sale_order_id.amount_total
            posted = rec.invoice_ids.filtered(
                lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
            )
            rec.amount_invoiced = sum(posted.mapped('amount_total'))
            # المدفوع فعليًا = المفوتر ناقص متبقّي الفواتير المرحَّلة
            rec.amount_paid = rec.amount_invoiced - sum(posted.mapped('amount_residual'))
            # المتبقي = المتبقّي على كامل الطلب (الإجمالي ناقص المدفوع)،
            # ويشمل الجزء الذي لم تُصدَر له فاتورة بعد. لا يقلّ عن صفر.
            rec.amount_due = max(0.0, rec.amount_total - rec.amount_paid)
            rec.payment_percentage = (
                (rec.amount_paid / rec.amount_total) * 100.0
                if rec.amount_total else 0.0
            )

    @api.depends(
        'sale_order_id.date_order',
        'invoice_ids.invoice_date',
        'payment_ids.date',
        'installment_line_ids.due_date',
        'installment_line_ids.state',
        'installment_line_ids.amount_due',
    )
    def _compute_dates(self):
        for rec in self:
            rec.order_date = (
                fields.Date.to_date(rec.sale_order_id.date_order)
                if rec.sale_order_id.date_order else False
            )
            inv_dates = rec.invoice_ids.filtered('invoice_date').mapped('invoice_date')
            rec.first_invoice_date = min(inv_dates) if inv_dates else False
            pay_dates = rec.payment_ids.filtered('date').mapped('date')
            rec.last_payment_date = max(pay_dates) if pay_dates else False
            unpaid = rec.installment_line_ids.filtered(
                lambda l: l.state != 'paid'
            ).sorted(key=lambda l: (l.due_date or date.max, l.sequence, l.id))
            next_line = unpaid[:1]
            rec.next_payment_date = next_line.due_date if next_line else False
            rec.next_payment_amount = next_line.amount_due if next_line else 0.0

    @api.depends(
        'installment_line_ids',
        'installment_line_ids.state',
        'installment_line_ids.days_overdue',
    )
    def _compute_installment_stats(self):
        for rec in self:
            lines = rec.installment_line_ids
            rec.installment_count = len(lines)
            rec.paid_installments = len(lines.filtered(lambda l: l.state == 'paid'))
            rec.overdue_installments = len(lines.filtered(lambda l: l.state == 'overdue'))
            rec.days_overdue = max(lines.mapped('days_overdue'), default=0)

    @api.depends(
        'amount_paid', 'amount_total', 'overdue_installments',
        'sale_order_id.state',
    )
    def _compute_state(self):
        for rec in self:
            currency = rec.currency_id or rec.company_id.currency_id
            if currency:
                fully_paid = currency.compare_amounts(rec.amount_paid, rec.amount_total) >= 0
            else:
                fully_paid = rec.amount_paid >= rec.amount_total
            if rec.sale_order_id.state == 'cancel':
                rec.state = 'cancelled'
            elif fully_paid:
                # يشمل الطلبات صفرية الإجمالي (لا شيء مستحق) ⟵ تُعدّ مدفوعة
                rec.state = 'paid'
            elif rec.overdue_installments > 0:
                rec.state = 'overdue'
            elif rec.amount_paid > 0:
                rec.state = 'in_progress'
            else:
                rec.state = 'pending'

    @api.depends('company_id')
    def _compute_waha_session_active(self):
        # وحدة WAHA تضيف الحقل default_whatsapp_session_id إلى res.company؛
        # نتحقق من وجوده أولًا حتى لا نتعطّل إن لم تكن الوحدة مثبّتة.
        has_field = 'default_whatsapp_session_id' in self.env['res.company']._fields
        for rec in self:
            session = rec.company_id.default_whatsapp_session_id if has_field else False
            rec.waha_session_active = bool(session) and session.status == 'working'

    def _compute_last_activity_on(self):
        """أحدث لحظة نشاط = الأكبر بين: تاريخ آخر تعديل (write_date)،
        وأحدث رسالة/ملاحظة في الشاتر، وأحدث نشاط (activity)."""
        for rec in self:
            candidates = [rec.write_date]
            message_dates = rec.message_ids.mapped('date')
            if message_dates:
                candidates.append(max(message_dates))
            activity_dates = rec.activity_ids.mapped('write_date')
            if activity_dates:
                candidates.append(max(activity_dates))
            candidates = [d for d in candidates if d]
            rec.last_activity_on = max(candidates) if candidates else rec.write_date

    def _search_last_activity_on(self, operator, value):
        """يبحث عبر المصادر الثلاثة معًا: تعديل السجل، أو نشاط، أو رسالة في الشاتر.
        تُحوّل النواة التعبير الزمني النسبي (مثل '-1d') مسبقًا لأن الحقل مباشر،
        ونحوّله يدويًا احتياطًا إن وصل كنص (المسارات المنقّطة لا تُحوَّل تلقائيًا).

        بما أن last_activity_on = القيمة القصوى للمصادر الثلاثة، فإن «أحدث من/منذ»
        (>= و >) تُكافئ OR على المصادر — وهي العوامل التي تستخدمها الفلاتر.
        للعوامل الأخرى (غير المستخدمة) نكتفي بتاريخ آخر تعديل كتقريب آمن، تفاديًا
        لتركيبة OR غير الصحيحة على مصادر متعددة (سجلات بلا رسائل/أنشطة)."""
        if isinstance(value, str):
            value = parse_date(value, self.env)
        if operator in ('>=', '>'):
            return [
                '|', '|',
                ('write_date', operator, value),
                ('message_ids.date', operator, value),
                ('activity_ids.write_date', operator, value),
            ]
        return [('write_date', operator, value)]

    # ==================================================================
    # توليد الأقساط
    # ==================================================================
    def _compute_due_date(self, base_date, nb_days, delay_type):
        """احسب تاريخ استحقاق القسط اعتمادًا على نوع التأخير في سياسة الدفع."""
        if not base_date:
            base_date = fields.Date.context_today(self)
        if isinstance(base_date, str):
            base_date = fields.Date.from_string(base_date)
        nb_days = nb_days or 0
        if delay_type == 'days_after_end_of_month':
            return base_date + relativedelta(day=31) + relativedelta(days=nb_days)
        if delay_type == 'days_after_end_of_next_month':
            return base_date + relativedelta(months=1, day=31) + relativedelta(days=nb_days)
        if delay_type == 'days_end_of_month_on_the':
            return base_date + relativedelta(days=nb_days) + relativedelta(day=31)
        # 'days_after' والافتراضي
        return base_date + relativedelta(days=nb_days)

    def _generate_installments(self):
        """يحذف الأقساط القديمة ويعيد بناءها من سياسة الدفع."""
        Line = self.env['payment.tracking.line']
        for rec in self:
            rec.installment_line_ids.unlink()
            amount_total = rec.amount_total or rec.sale_order_id.amount_total
            currency = rec.currency_id or rec.company_id.currency_id
            base_date = rec.order_date or fields.Date.to_date(
                rec.sale_order_id.date_order
            ) or fields.Date.context_today(rec)
            term = rec.payment_term_id

            if not term or not term.line_ids:
                # لا توجد سياسة دفع: قسط واحد بكامل المبلغ
                if amount_total:
                    Line.create({
                        'tracking_id': rec.id,
                        'sequence': 10,
                        'name': _('دفعة كاملة'),
                        'amount_expected': amount_total,
                        'due_date': base_date,
                    })
                continue

            lines = term.line_ids.sorted(key=lambda l: (l.nb_days, l.id))
            running = 0.0
            vals_list = []
            for idx, line in enumerate(lines, start=1):
                if line.value == 'percent':
                    amount = amount_total * (line.value_amount / 100.0)
                elif line.value == 'fixed':
                    amount = line.value_amount
                else:  # 'balance' أو أي قيمة أخرى: المتبقي
                    amount = amount_total - running
                if currency:
                    amount = currency.round(amount)
                running += amount
                due_date = rec._compute_due_date(base_date, line.nb_days, line.delay_type)
                vals_list.append({
                    'tracking_id': rec.id,
                    'sequence': idx * 10,
                    'name': _('القسط %s') % idx,
                    'payment_term_line_id': line.id,
                    'amount_expected': amount,
                    'due_date': due_date,
                })
            # تسوية فروقات التقريب على القسط الأخير
            if vals_list and currency:
                diff = currency.round(amount_total - running)
                if diff:
                    vals_list[-1]['amount_expected'] = currency.round(
                        vals_list[-1]['amount_expected'] + diff
                    )
            Line.create(vals_list)

    # ==================================================================
    # ORM overrides
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) in (False, _('New'), '/', 'New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'payment.tracking.sequence'
                ) or _('New')
        records = super().create(vals_list)
        for rec in records:
            rec._generate_installments()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'sale_order_id' in vals:
            for rec in self:
                rec._generate_installments()
        return res

    # ==================================================================
    # الأزرار / الإجراءات
    # ==================================================================
    @api.model
    def _get_whatsapp_safe_fields(self):
        """الحقول المسموح استخدامها كمتغيّرات في قوالب واتساب لهذا النموذج.

        تتيح لمسؤول واتساب غير النظامي تحرير/إعادة اعتماد قالب التذكير دون
        خطأ صلاحيات (قيد whatsapp.template.variable._check_field_name).
        """
        return {
            'partner_id', 'sale_order_id', 'amount_due',
            'currency_id.name', 'next_payment_date',
        }

    def _get_reminder_message(self):
        """يبني نص رسالة التذكير المعبّأ مسبقًا لمعالج WAHA."""
        self.ensure_one()

        def _fmt(amount):
            return '%s %s' % (
                '{:,.2f}'.format(amount or 0.0),
                self.currency_id.name or '',
            )

        lines = [
            _('عميلنا العزيز %s،') % (self.partner_id.name or ''),
            '',
            _('نود تذكيركم بشأن الدفعات المستحقة على الطلب %s:') % (
                self.sale_order_id.name or self.name
            ),
            _('• إجمالي الطلب: %s') % _fmt(self.amount_total),
            _('• المبلغ المدفوع: %s') % _fmt(self.amount_paid),
            _('• المبلغ المتبقي: %s') % _fmt(self.amount_due),
        ]
        if self.next_payment_date:
            lines.append(
                _('• الدفعة القادمة: %s بتاريخ %s') % (
                    _fmt(self.next_payment_amount), self.next_payment_date
                )
            )
        lines.extend(['', _('شكرًا لتعاونكم.')])
        return '\n'.join(lines)

    def action_send_whatsapp_reminder(self):
        """زر «واتساب (WAHA)»: يفتح معالج WAHA معبّأً برقم الجوال ونص التذكير.

        أما زر «واتساب للأعمال» فيستدعي مباشرةً إجراء النافذة
        action_payment_tracking_whatsapp (التكامل القياسي whatsapp.composer).
        """
        self.ensure_one()
        if 'sadeem.waha.send.whatsapp.wizard' not in self.env:
            raise UserError(_('وحدة WAHA لواتساب غير مثبّتة على النظام.'))
        if not self.partner_mobile:
            raise UserError(_('لا يوجد رقم جوال للعميل لإرسال رسالة واتساب.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('إرسال تذكير عبر واتساب (WAHA)'),
            'res_model': 'sadeem.waha.send.whatsapp.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'payment.tracking',
                'active_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_phone_number': self.partner_mobile,
                'default_message_text': self._get_reminder_message(),
            },
        }

    def action_send_email_reminder(self):
        self.ensure_one()
        if not self.partner_email:
            raise UserError(_('لا يوجد بريد إلكتروني مسجّل للعميل.'))
        template = self.env.ref(
            'era_payment_tracking_dashboard.email_payment_reminder',
            raise_if_not_found=False,
        )
        ctx = {
            'default_model': 'payment.tracking',
            'default_res_ids': self.ids,
            'default_composition_mode': 'comment',
            'default_email_layout_xmlid': 'mail.mail_notification_light',
            'force_email': True,
        }
        if template:
            ctx['default_template_id'] = template.id
            ctx['default_use_template'] = True
        return {
            'type': 'ir.actions.act_window',
            'name': _('إرسال تذكير بالبريد الإلكتروني'),
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
            'context': ctx,
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('الفواتير'),
            'res_model': 'account.move',
            'domain': [('id', 'in', self.invoice_ids.ids)],
            'view_mode': 'list,form',
            'context': {'create': False},
        }

    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('الدفعات'),
            'res_model': 'account.payment',
            'domain': [('id', 'in', self.payment_ids.ids)],
            'view_mode': 'list,form',
            'context': {'create': False},
        }

    def action_refresh_data(self):
        """يعيد توليد الأقساط ويجبر إعادة احتساب المبالغ."""
        self._generate_installments()
        self.invalidate_recordset()
        if self.env.context.get('from_button'):
            return {'type': 'ir.actions.client', 'tag': 'reload'}
        return True

    # ==================================================================
    # جلب الأوامر الموجودة مسبقًا (Backfill)
    # ==================================================================
    @api.model
    def _backfill_existing_orders(self):
        """ينشئ متابعة لكل أمر بيع مؤكَّد لا يملك متابعة بعد.

        تُستدعى تلقائيًا عند تثبيت الوحدة (post_init_hook) ويمكن إعادة
        تشغيلها يدويًا من القائمة لتغطية الأوامر المؤكدة قبل التثبيت أو
        الأوامر المستوردة لاحقًا.
        """
        orders = self.env['sale.order'].search([('state', '=', 'sale')])
        # نمنع التكرار عبر sudo حتى لو كان بعض المتابعات خارج نطاق رؤية المستخدم
        existing_order_ids = set(self.sudo().search([]).mapped('sale_order_id').ids)
        todo = orders.filtered(lambda o: o.id not in existing_order_ids)
        if not todo:
            return self.browse()
        return self.create([{'sale_order_id': order.id} for order in todo])

    # ==================================================================
    # المهام المجدولة (Cron)
    # ==================================================================
    @api.model
    def _cron_payment_reminders(self):
        """ينشئ نشاط تذكير للمسؤول عن السجلات المتأخرة أكثر من 3 أيام."""
        overdue = self.search([
            ('state', '=', 'overdue'),
            ('days_overdue', '>', 3),
        ])
        for rec in overdue:
            user = rec.sale_order_id.user_id or rec.create_uid or self.env.user
            summary = _('متابعة دفعة متأخرة: %s') % rec.name
            # تجنّب تكرار النشاط نفسه إن كان مفتوحًا بالفعل
            existing = rec.activity_ids.filtered(
                lambda a: a.summary == summary and a.user_id == user
            )
            if existing:
                continue
            rec.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=user.id,
                summary=summary,
                note=_(
                    'الطلب %(order)s للعميل %(partner)s متأخر %(days)s يومًا. '
                    'المبلغ المتبقي: %(amount)s.'
                ) % {
                    'order': rec.sale_order_id.name or rec.name,
                    'partner': rec.partner_id.name or '',
                    'days': rec.days_overdue,
                    'amount': rec.amount_due,
                },
            )
        return True
