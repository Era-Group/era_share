# -*- coding: utf-8 -*-
"""Admin wizard to bulk-manage `yusr_visible` on every hr.leave.type.

Why this exists: on this instance a few hr.leave.type records are
hidden from the normal Time Off > Configuration > Leave Types list
view (record rules or company scoping), so HR can't reach the
`Show in Yusr Mobile App` checkbox on those records from the
standard form. This wizard reads every hr.leave.type via sudo —
bypassing record rules — and exposes a simple list with a single
editable column so HR can flip visibility per type.
"""
from odoo import api, fields, models, _


class YusrLeaveTypeVisibilityWizard(models.TransientModel):
    _name = 'yusr.leave.type.visibility.wizard'
    _description = 'Yusr — Bulk leave type visibility'

    line_ids = fields.One2many(
        'yusr.leave.type.visibility.line',
        'wizard_id',
        string='Leave Types',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # sudo + active_test=False → every hr.leave.type row, regardless
        # of record rules, company scoping, or archive state.
        types = (
            self.env['hr.leave.type']
            .sudo()
            .with_context(active_test=False)
            .search([], order='active desc, name')
        )
        res['line_ids'] = [(0, 0, {
            'leave_type_id': t.id,
            'name': t.name,
            'company_name': t.company_id.name if t.company_id else _('All companies'),
            'active': t.active,
            'employee_requests': t.employee_requests,
            'yusr_visible': t.yusr_visible,
        }) for t in types]
        return res

    def action_apply(self):
        self.ensure_one()
        for line in self.line_ids:
            # Use sudo here too: the target record may be outside the
            # current user's company scope, and we already chose to
            # trust HR enough to let them edit this wizard.
            line.leave_type_id.sudo().write({
                'yusr_visible': line.yusr_visible,
            })
        return {'type': 'ir.actions.act_window_close'}


class YusrLeaveTypeVisibilityLine(models.TransientModel):
    _name = 'yusr.leave.type.visibility.line'
    _description = 'Yusr visibility line'
    _order = 'active desc, name'

    wizard_id = fields.Many2one(
        'yusr.leave.type.visibility.wizard',
        required=True,
        ondelete='cascade',
    )
    leave_type_id = fields.Many2one(
        'hr.leave.type',
        string='Leave Type',
        required=True,
        readonly=True,
    )
    name = fields.Char(string='Name', readonly=True)
    company_name = fields.Char(string='Company', readonly=True)
    active = fields.Boolean(string='Active in Odoo', readonly=True)
    employee_requests = fields.Selection(
        [('yes', 'Yes'), ('no', 'No')],
        string='Employee Requests',
        readonly=True,
    )
    yusr_visible = fields.Boolean(string='Show in Yusr App')
