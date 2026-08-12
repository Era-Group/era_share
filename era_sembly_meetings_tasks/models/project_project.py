# -*- coding: utf-8 -*-
"""Requirement 5 — the meetings smart button on a project.

The project count deliberately includes meetings attached to any of the
project's TASKS — including the auto-created "الاجتماعات" bucket task — because
a project whose meetings all live on that bucket would otherwise show zero.
"""
from odoo import _, fields, models


class ProjectProject(models.Model):
    _inherit = 'project.project'

    sembly_meeting_count = fields.Integer(
        string="عدد الاجتماعات", compute='_compute_sembly_meeting_count')

    def _sembly_meeting_domain(self):
        self.ensure_one()
        return ['|', ('project_id', '=', self.id), ('task_id.project_id', '=', self.id)]

    def _compute_sembly_meeting_count(self):
        Meeting = self.env['sembly.meeting'].sudo()
        # Two cheap grouped reads beat one domain per project (no N+1).
        direct = dict(Meeting._read_group(
            [('project_id', 'in', self.ids)], ['project_id'], ['__count'])) if self.ids else {}
        via_task = dict(Meeting._read_group(
            [('task_id.project_id', 'in', self.ids), ('project_id', 'not in', self.ids)],
            ['task_id'], ['__count'])) if self.ids else {}
        # Fold the task-side counts up to their project.
        task_counts = {}
        for task, count in via_task.items():
            task_counts[task.project_id.id] = task_counts.get(task.project_id.id, 0) + count
        for project in self:
            project.sembly_meeting_count = (
                direct.get(project, 0) + task_counts.get(project.id, 0))

    def action_view_sembly_meetings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("الاجتماعات"),
            'res_model': 'sembly.meeting',
            'view_mode': 'list,kanban,form',
            'domain': self._sembly_meeting_domain(),
            'context': {'default_project_id': self.id},
        }
