# -*- coding: utf-8 -*-
"""Requirement 5 — the meetings smart button on a task."""
from odoo import _, fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    sembly_meeting_ids = fields.One2many(
        'sembly.meeting', 'task_id', string="اجتماعات Sembly")
    sembly_meeting_count = fields.Integer(
        string="عدد الاجتماعات", compute='_compute_sembly_meeting_count')

    def _compute_sembly_meeting_count(self):
        counts = {}
        if self.ids:
            groups = self.env['sembly.meeting'].sudo()._read_group(
                [('task_id', 'in', self.ids)], ['task_id'], ['__count'])
            counts = {task.id: count for task, count in groups}
        for task in self:
            task.sembly_meeting_count = counts.get(task.id, 0)

    def action_view_sembly_meetings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("الاجتماعات"),
            'res_model': 'sembly.meeting',
            'view_mode': 'list,kanban,form',
            'domain': [('task_id', '=', self.id)],
            'context': {
                'default_task_id': self.id,
                'default_project_id': self.project_id.id,
            },
        }
