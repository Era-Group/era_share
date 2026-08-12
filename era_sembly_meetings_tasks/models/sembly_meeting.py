# -*- coding: utf-8 -*-
"""The project and task links, plugged into the seams the base app defines.

This module owns two of the four link fields because they are not independent:
a task implies its project, and a project with no task of its own gets the
"الاجتماعات" bucket task. Splitting them across two modules would put that
relationship across a module boundary for no benefit.
"""
from odoo import api, fields, models
from odoo.tools import plaintext2html

# Hard caps on what reaches the LLM.
MAX_PROJECT_CANDIDATES = 50
MAX_TASK_CANDIDATES = 40
# Prompt block order: after the opportunities, before the tickets.
PROJECT_POOL_SEQUENCE = 20
TASK_POOL_SEQUENCE = 30

DEFAULT_MEETINGS_TASK_NAME = 'الاجتماعات'


def _sembly_tasks_post_init(env):
    """Seed the two bucket-task settings, but ONLY if they are absent.

    They are deliberately not shipped as an ``ir.config_parameter`` data file.
    Before the four-way split they belonged to the base module, so on an
    existing database the rows already exist: a data file would either collide
    with the unique index on ``key``, or — once the xmlid is re-owned — quietly
    overwrite a value the customer had tuned. "Create if missing" is the only
    behaviour that is correct on both a fresh and an upgraded database.
    """
    icp = env['ir.config_parameter'].sudo()
    for key, default in (('sembly.meetings_task_name', DEFAULT_MEETINGS_TASK_NAME),
                         ('sembly.auto_create_meetings_task', '1')):
        if icp.get_param(key) is False:
            icp.set_param(key, default)


class SemblyMeeting(models.Model):
    _inherit = 'sembly.meeting'

    project_id = fields.Many2one(
        'project.project', string="المشروع", ondelete='set null',
        index=True, tracking=True)
    task_id = fields.Many2one(
        'project.task', string="التاسك", ondelete='set null',
        index=True, tracking=True)

    # ------------------------------------------------------------------ seams
    @api.model
    def _sembly_link_field_by_model(self):
        """SEAM — lets the base file an internal meeting here, and puts this
        model in the settings picker, without the base naming it."""
        return dict(super()._sembly_link_field_by_model(), **{'project.project': 'project_id', 'project.task': 'task_id'})

    @api.model
    def _sembly_link_fields(self):
        return super()._sembly_link_fields() | {'project_id', 'task_id'}

    def _ai_candidate_pools(self):
        """Contribute the project and task pools, narrowed DETERMINISTICALLY.

        Three signals: the participants' own records, significant words of the
        meeting title, and the ambient set of projects and still-open tasks —
        the last one because a delivery meeting very often concerns an open
        task nobody in the room is the "customer" of.
        """
        self.ensure_one()
        pools = super()._ai_candidate_pools()
        Project = self.env['project.project'].sudo()
        Task = self.env['project.task'].sudo()

        projects = Project.browse()
        tasks = Task.browse()
        basis = []

        partners = self._candidate_partners()
        if partners:
            projects |= Project.search(
                [('partner_id', 'in', partners.ids)], limit=20)
            tasks |= Task.search(
                [('partner_id', 'in', partners.ids)],
                order='write_date desc', limit=MAX_TASK_CANDIDATES)
            basis.append('participants(%d)' % len(partners))

        tokens = self._title_tokens()[:6]
        if tokens:
            for token in tokens:
                projects |= Project.search([('name', 'ilike', token)], limit=10)
                tasks |= Task.search([('name', 'ilike', token)],
                                     order='write_date desc', limit=15)
            basis.append('title-tokens(%s)' % ",".join(tokens))

        # The ambient set. Databases carry far fewer projects than leads, so
        # listing them is cheap and materially improves recall.
        projects |= Project.search([], limit=MAX_PROJECT_CANDIDATES)
        tasks |= Task.search([('is_closed', '=', False)],
                             order='write_date desc', limit=80)
        basis.append('open-tasks')

        truncated = []
        if len(projects) > MAX_PROJECT_CANDIDATES:
            projects = projects[:MAX_PROJECT_CANDIDATES]
            truncated.append('projects>%d' % MAX_PROJECT_CANDIDATES)
        if len(tasks) > MAX_TASK_CANDIDATES:
            tasks = tasks[:MAX_TASK_CANDIDATES]
            truncated.append('tasks>%d' % MAX_TASK_CANDIDATES)
        if truncated:
            basis.append('TRUNCATED:' + ",".join(truncated))

        signals = ",".join(basis) or 'none'
        pools.append({
            'key': 'project_id',
            'label': "CANDIDATE PROJECTS (id | name | customer)",
            'records': projects,
            'render': lambda rec: '%s | %s | customer: %s' % (
                rec.id, rec.name, rec.partner_id.display_name or '-'),
            'sequence': PROJECT_POOL_SEQUENCE,
            'basis': 'projects(%s)' % signals,
        })
        pools.append({
            'key': 'task_id',
            'label': "CANDIDATE TASKS (id | name | project)",
            'records': tasks,
            'render': lambda rec: '%s | %s | project: %s' % (
                rec.id, rec.name, rec.project_id.display_name or '-'),
            'sequence': TASK_POOL_SEQUENCE,
            'basis': 'tasks(%s)' % signals,
        })
        return pools

    def _ai_postprocess_links(self, links, pools):
        """A chosen task implies its project.

        The prompt asks for both, but a model that returns only ``task_id``
        must still produce a complete link rather than a task with no project.
        """
        links = super()._ai_postprocess_links(links, pools)
        if links.get('task_id') and not links.get('project_id'):
            task = self.env['project.task'].sudo().browse(links['task_id'])
            links = dict(links, project_id=task.project_id.id or False)
        return links

    def _ai_after_link(self):
        """A project-only match is filed under the bucket task."""
        result = super()._ai_after_link()
        self._ensure_meetings_task()
        return result

    def _summary_targets(self):
        """Requirement 6: the task receives the internal note.

        The PROJECT deliberately does not — a project-only match already ends
        up on the bucket task, so posting on both would duplicate the note.
        """
        targets = super()._summary_targets()
        if self.task_id:
            targets.append(self.task_id)
        return targets

    # ------------------------------------------------------------------ bucket
    def _ensure_meetings_task(self):
        """File a project-only match under a per-project bucket task.

        Only applies when a project matched but no task did and nothing more
        specific (an opportunity, a ticket) claimed the meeting first. The
        search runs before the create, so parallel cron runs converge on one
        task per project rather than racing to create two.
        """
        self.ensure_one()
        enabled = self._icp('sembly.auto_create_meetings_task', '1')
        if enabled not in ('1', 'True', 'true'):
            return False
        if self.task_id or not self.project_id:
            return False
        if self._has_external_link():
            return False

        name = self._icp('sembly.meetings_task_name') or DEFAULT_MEETINGS_TASK_NAME
        Task = self.env['project.task'].sudo()
        task = Task.search([
            ('project_id', '=', self.project_id.id), ('name', '=', name),
        ], limit=1)
        if not task:
            task = Task.create({
                'name': name,
                'project_id': self.project_id.id,
                'description': plaintext2html(
                    "Auto-created by the Sembly integration to collect meetings "
                    "that belong to this project but to no specific task."),
            })
        self.sudo().with_context(sembly_sync=True).write({'task_id': task.id})
        return True

    # ------------------------------------------------------------------ actions
    def action_open_linked_project(self):
        return self._action_open_link('project_id')

    def action_open_linked_task(self):
        return self._action_open_link('task_id')
