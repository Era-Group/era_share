# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class ProjectBrdScopeItem(models.Model):
    _name = 'project.brd.scope.item'
    _description = 'Project BRD Contract Scope Reconciliation Item'
    _order = 'project_id, brd_version, sequence, id'

    project_id = fields.Many2one(
        'project.project', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', required=True, index=True)
    active = fields.Boolean(default=True, required=True, index=True)
    brd_version = fields.Integer(required=True, index=True)
    scope_run = fields.Integer(required=True, index=True)
    project_brd_state = fields.Selection(
        related='project_id.brd_state', readonly=True)
    can_review = fields.Boolean(compute='_compute_can_review')
    sequence = fields.Integer(required=True, default=10)
    code = fields.Char(required=True, index=True)
    requirement = fields.Text(required=True)
    source_reference = fields.Text(required=True)
    ai_classification = fields.Selection([
        ('in_scope', 'ضمن النطاق'),
        ('out_of_scope', 'خارج النطاق صراحةً'),
        ('change_candidate', 'مرشح طلب تغيير'),
        ('unclear', 'غير محسوم'),
        ('deferred', 'مؤجل'),
    ], required=True, readonly=True)
    classification = fields.Selection([
        ('in_scope', 'ضمن النطاق'),
        ('out_of_scope', 'خارج النطاق صراحةً'),
        ('change_candidate', 'مرشح طلب تغيير'),
        ('unclear', 'غير محسوم'),
        ('deferred', 'مؤجل'),
    ], required=True, index=True)
    confidence = fields.Selection([
        ('high', 'مرتفعة'),
        ('medium', 'متوسطة'),
        ('low', 'منخفضة'),
    ], required=True)
    contract_reference = fields.Text(required=True)
    reason = fields.Text(required=True)
    impact = fields.Text(required=True)
    recommended_action = fields.Text(required=True)
    manager_decision_reason = fields.Selection([
        ('contract_explicitly_covers',
         'نص العقد يذكر العمل صراحةً ضمن النطاق'),
        ('standard_configuration_only',
         'العمل مغطى ضمن إعداد Odoo القياسي فقط'),
        ('customization_beyond_standard',
         'الطلب تخصيص إضافي يتجاوز الإعداد القياسي المتفق عليه'),
        ('contract_explicitly_excludes',
         'نص العقد يستبعد العمل صراحةً'),
        ('not_covered_change_required',
         'لا يوجد بند يغطي العمل؛ يلزم طلب تغيير وتسعير مستقل'),
        ('deferred_by_agreement',
         'يوجد اتفاق مكتوب على تأجيل العمل لمرحلة لاحقة'),
        ('customer_confirms_in_scope',
         'العميل أكد كتابياً أن العمل ضمن العقد الحالي'),
        ('customer_confirms_change_request',
         'العميل أكد كتابياً أن العمل طلب تغيير خارج العقد'),
    ], string="أساس قرار المدير",
        help="The explicit contractual or written basis for overriding the AI scope classification.")
    manager_note = fields.Text(string="تفاصيل إضافية")
    cr_number = fields.Char(string="رقم طلب التغيير")
    commercial_status = fields.Selection([
        ('pending_assessment', 'بانتظار التقييم'),
        ('pricing', 'قيد التسعير'),
        ('submitted', 'مقدم للعميل'),
        ('approved', 'معتمد'),
        ('rejected', 'مرفوض'),
        ('included_no_charge', 'مشمول دون تكلفة'),
        ('deferred', 'مؤجل'),
    ], default='pending_assessment', required=True, index=True)

    _project_version_code_unique = models.Constraint(
        'UNIQUE(project_id, brd_version, scope_run, code)',
        'Each scope reconciliation item must have a unique code per BRD version.')

    @api.depends_context('uid')
    def _compute_can_review(self):
        can_review = self.env.user.has_group('project.group_project_manager')
        for item in self:
            item.can_review = can_review

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(
                'project.group_project_manager'):
            raise AccessError(_(
                "Only a project manager can add BRD scope review items."))
        if not self.env.su:
            projects = self.env['project.project'].browse(
                [vals.get('project_id') for vals in vals_list])
            if any(project.brd_state != 'scope_review' for project in projects):
                raise UserError(_(
                    "Scope items can only be added during scope review."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group(
                'project.group_project_manager'):
            raise AccessError(_(
                "Only a project manager can edit BRD scope review items."))
        commercial_fields = {
            'cr_number', 'commercial_status', 'manager_decision_reason',
            'manager_note', 'impact', 'recommended_action',
        }
        if not self.env.su and any(
                item.project_id.brd_state != 'scope_review'
                for item in self) and not set(vals).issubset(commercial_fields):
            raise UserError(_(
                "Approved scope classifications are locked. Only commercial "
                "follow-up fields can be updated."))
        result = super().write(vals)
        if commercial_fields.intersection(vals):
            for project in self.project_id.filtered(
                    lambda record: record.brd_state == 'done'):
                project._brd_sync_scope_document()
        return result

    def unlink(self):
        if not self.env.su and not self.env.user.has_group(
                'project.group_project_manager'):
            raise AccessError(_(
                "Only a project manager can remove BRD scope review items."))
        if not self.env.su and any(
                item.project_id.brd_state != 'scope_review' for item in self):
            raise UserError(_(
                "Scope items can only be removed during scope review."))
        return super().unlink()
